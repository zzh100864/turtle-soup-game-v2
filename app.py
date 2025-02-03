# -*- coding: utf-8 -*-
import requests
from flask import Flask, request, jsonify, send_from_directory
from functools import wraps
import time
import hashlib
from flask_cors import CORS
import json
import threading
import base64
import hmac
import datetime
from urllib.parse import urlparse, urlencode
import ssl
import websocket
import uuid
import pickle
import os

app = Flask(__name__)
CORS(app)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev')

# API配置
API_KEY = "sparkUltra4.0"
VALID_PERIOD = 30 * 60  # 30分钟有效期

# 讯飞星火配置
SPARKAI_URL = "wss://spark-api.xf-yun.com/v4.0/chat"
SPARKAI_APP_ID = '6126ea78'
SPARKAI_API_SECRET = 'OTgxMmNmNjlhNjcxOGI5YTZkZjI5NWZk'
SPARKAI_API_KEY = '9a551368ad829cf9d47e9cc22716a179'
SPARKAI_DOMAIN = '4.0Ultra'

# 单人模式全局变量
current_scenario = None  # 当前谜题场景
question_count = 0      # 问题计数
hint_count = 2         # 提示次数
answer_attempts = 3  # 单人模式答案尝试次数
correct_answer = None  # 存储正确答案

# 多人模式全局变量
rooms = {}  # 存储所有游戏房间

# 在全局变量部分添加分类定义
STORY_CATEGORIES = {
    'type': {
        'horror': '恐怖惊悚',
        'mystery': '悬疑推理',
        'emotion': '情感故事',
        'fantasy': '奇幻冒险',
        'logic': '逻辑推理',
        'career': '职业故事'
    },
    'difficulty': {
        'easy': '简单入门',
        'medium': '中等难度', 
        'hard': '高难度挑战'
    },
    'theme': {
        'daily': '日常生活',
        'history': '历史文化',
        'scifi': '科幻未来',
        'fairy': '童话神话'
    }
}

# 添加持久化相关的函数
ROOMS_FILE = 'rooms.pkl'

def save_rooms():
    try:
        with open(ROOMS_FILE, 'wb') as f:
            pickle.dump(rooms, f)
        print(f"Saved rooms: {list(rooms.keys())}")
    except Exception as e:
        print(f"Error saving rooms: {e}")

def load_rooms():
    global rooms
    try:
        if os.path.exists(ROOMS_FILE):
            with open(ROOMS_FILE, 'rb') as f:
                rooms = pickle.load(f)
            print(f"Loaded rooms: {list(rooms.keys())}")
        else:
            rooms = {}
            print("No saved rooms found")
    except Exception as e:
        print(f"Error loading rooms: {e}")
        rooms = {}

class Room:
    def __init__(self, room_id, host_name, story_type=None, difficulty=None, theme=None):
        self.room_id = room_id
        self.host_name = host_name
        self.players = [{
            'name': host_name,
            'score': 0,
            'is_host': True
        }]
        self.current_scenario = None
        self.question_count = 0
        self.hint_count = 2
        self.status = 'waiting'  # waiting, playing, finished
        self.current_player = 0
        self.last_activity = time.time()
        self.answer_attempts = len(self.players) * 2
        self.correct_answer = None
        self.chat_messages = []
        # 添加分类信息
        self.story_type = story_type
        self.difficulty = difficulty
        self.theme = theme
        # 添加加入请求列表
        self.join_requests = []  # 存储等待审核的加入请求

# 修改清理不活跃的房间函数
def cleanup_inactive_rooms():
    current_time = time.time()
    inactive_threshold = 60 * 60  # 改为60分钟无活动的房间将被清理
    
    print(f"Cleaning up rooms. Current rooms: {list(rooms.keys())}")  # 添加调试日志
    
    for room_id in list(rooms.keys()):
        room = rooms[room_id]
        inactive_time = current_time - room.last_activity
        if inactive_time > inactive_threshold:
            print(f"Removing inactive room {room_id} (inactive for {inactive_time/60:.1f} minutes)")
            del rooms[room_id]
    
    print(f"Rooms after cleanup: {list(rooms.keys())}")  # 添加调试日志
    save_rooms()  # 保存清理后的房间数据

# 修改清理线程的间隔
def room_cleanup_thread():
    while True:
        cleanup_inactive_rooms()
        time.sleep(600)  # 改为每10分钟清理一次

# 启动清理线程
cleanup_thread = threading.Thread(target=room_cleanup_thread, daemon=True)
cleanup_thread.start()

def generate_api_key():
    timestamp = str(int(time.time()))
    raw_key = f"{API_KEY}:{timestamp}"
    return f"{hashlib.md5(raw_key.encode()).hexdigest()}:{timestamp}"

def verify_api_key(key_with_timestamp):
    try:
        if not key_with_timestamp or ':' not in key_with_timestamp:
            return False
        received_hash, timestamp = key_with_timestamp.split(':')
        timestamp = int(timestamp)
        if int(time.time()) - timestamp > VALID_PERIOD:
            return False
        raw_key = f"{API_KEY}:{timestamp}"
        expected_hash = hashlib.md5(raw_key.encode()).hexdigest()
        return received_hash == expected_hash
    except:
        return False

def get_request_data():
    """安全地获取请求数据"""
    try:
        if request.is_json:
            try:
                return request.json or {}
            except Exception:
                return {}
        elif request.form:
            try:
                # 最简单的方式处理表单
                return request.form
            except Exception:
                return {}
        elif request.data:
            try:
                if isinstance(request.data, bytes):
                    try:
                        return json.loads(request.data.decode('utf-8'))
                    except (UnicodeDecodeError, json.JSONDecodeError):
                        return {}
                return {}
            except Exception:
                return {}
        return {}
    except Exception:
        return {}

def require_api_key(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        try:
            data = get_request_data()
            if isinstance(data, dict):
                api_key = str(data.get('X_API_KEY', ''))
                if api_key and verify_api_key(api_key):
                    return f(*args, **kwargs)
        except Exception as e:
            return jsonify({'error': str(e)}), 400
            
        return jsonify({
            'error': '未授权访问',
            'message': '无效的API密钥或密钥已过期'
        }), 401
    return decorated_function

def get_spark_auth_url():
    """生成讯飞星火认证URL"""
    url = urlparse(SPARKAI_URL)
    
    # 生成RFC1123格式的时间戳
    date = datetime.datetime.utcnow().strftime('%a, %d %b %Y %H:%M:%S GMT')
    
    # 拼接字符串
    signature_origin = f"host: {url.netloc}\ndate: {date}\nGET {url.path} HTTP/1.1"
    
    # 使用hmac-sha256进行加密
    signature_sha = hmac.new(
        SPARKAI_API_SECRET.encode('utf-8'),
        signature_origin.encode('utf-8'),
        digestmod=hashlib.sha256
    ).digest()
    
    signature_sha_base64 = base64.b64encode(signature_sha).decode()
    authorization_origin = f'api_key="{SPARKAI_API_KEY}", algorithm="hmac-sha256", headers="host date request-line", signature="{signature_sha_base64}"'
    authorization = base64.b64encode(authorization_origin.encode('utf-8')).decode()
    
    # 组装鉴权参数
    v = {
        "authorization": authorization,
        "date": date,
        "host": url.netloc
    }
    
    # 生成鉴权url
    return SPARKAI_URL + '?' + urlencode(v)

@app.route('/generate_key', methods=['GET'])
def get_api_key():
    api_key = generate_api_key()
    return jsonify({
        'api_key': api_key,
        'expires_in': f'{VALID_PERIOD / 60}分钟'
    })

@app.route('/chat', methods=['POST'])
@require_api_key
def chat():
    try:
        data = get_request_data()
        if not data:
            return jsonify({'error': '需要 JSON 数据'}), 400
            
        message = data.get('message', '') if isinstance(data, dict) else ''
        if not message:
            return jsonify({'error': '消息不能为空'}), 400

        reply = send_message(message)
        return jsonify({
            'status': 'success',
            'response': reply
        })

    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500

# 单人模式路由
@app.route('/single/start_game', methods=['POST'])
@require_api_key
def single_start_game():
    global current_scenario, question_count, hint_count, answer_attempts, correct_answer
    try:
        data = get_request_data()
        story_type = data['story_type'] if isinstance(data, dict) and 'story_type' in data else None
        difficulty = data['difficulty'] if isinstance(data, dict) and 'difficulty' in data else None
        theme = data['theme'] if isinstance(data, dict) and 'theme' in data else None

        prompt = """作为海龟汤游戏主持人，请直接输出一个谜题场景。
格式要求：必须以"场景：["开头，以"]"结尾。

要求：
1. 不要有任何其他文字说明
2. 不要解释规则
3. 只输出一个场景描述
"""
        # 根据选择的分类添加提示
        if story_type:
            prompt += f"\n4. 故事类型为：{STORY_CATEGORIES['type'].get(story_type)}"
        if difficulty:
            prompt += f"\n5. 难度级别为：{STORY_CATEGORIES['difficulty'].get(difficulty)}"
        if theme:
            prompt += f"\n6. 故事主题为：{STORY_CATEGORIES['theme'].get(theme)}"

        # 尝试最多3次获取有效场景
        for _ in range(3):
            scenario_text = send_message(prompt)
            print("AI 返回的场景文本:", scenario_text)
            
            start_index = scenario_text.find("场景：[")
            end_index = scenario_text.find("]", start_index)
            
            if start_index != -1 and end_index != -1:
                current_scenario = scenario_text[start_index + 4:end_index]
                question_count = 0
                hint_count = 2
                answer_attempts = 3  # 重置答案尝试次数
                
                # 获取正确答案
                answer_prompt = f"基于场景：[{current_scenario}]，请详细解释这个谜题的答案。"
                correct_answer = send_message(answer_prompt)
                
                return jsonify({
                    'status': 'success',
                    'scenario': current_scenario,
                    'hint_count': hint_count,
                    'answer_attempts': answer_attempts
                })
                
        raise Exception("无法生成有效的场景，请重试")

    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500

@app.route('/single/ask_question', methods=['POST'])
@require_api_key
def single_ask_question():
    global current_scenario, question_count
    try:
        if not current_scenario:
            return jsonify({'error': '请先开始游戏'}), 400
            
        data = get_request_data()
        question = data['question'] if isinstance(data, dict) and 'question' in data else ''
        if not question:
            return jsonify({'error': '问题不能为空'}), 400

        prompt = f"基于场景：[{current_scenario}]，回答问题：{question}。只能回答'是'或'否'。"
        answer = send_message(prompt)
        question_count += 1
        
        return jsonify({
            'status': 'success',
            'answer': answer,
            'question_count': question_count
        })

    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500

@app.route('/single/request_hint', methods=['POST'])
@require_api_key
def single_request_hint():
    global current_scenario, hint_count
    try:
        if not current_scenario:
            return jsonify({'error': '请先开始游戏'}), 400
            
        if hint_count <= 0:
            return jsonify({'error': '没有剩余提示次数'}), 400
            
        prompt = f"基于场景：[{current_scenario}]，给出一个提示，帮助玩家理解情况，但不要直接透露答案。"
        hint = send_message(prompt)
        hint_count -= 1
        
        return jsonify({
            'status': 'success',
            'hint': hint,
            'remaining_hints': hint_count
        })

    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500

@app.route('/single/get_answer', methods=['POST'])
@require_api_key
def get_answer():
    global current_scenario
    try:
        if not current_scenario:
            return jsonify({'error': '请先开始游戏'}), 400
            
        prompt = f"基于场景：[{current_scenario}]，请详细解释这个谜题的答案。"
        answer = send_message(prompt)
        
        return jsonify({
            'status': 'success',
            'answer': answer
        })

    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500

@app.route('/single/check_answer', methods=['POST'])
@require_api_key
def check_answer():
    global current_scenario, answer_attempts, correct_answer
    try:
        if not current_scenario:
            return jsonify({'error': '请先开始游戏'}), 400
            
        if answer_attempts <= 0:
            return jsonify({'error': '已达到最大尝试次数'}), 400
            
        data = get_request_data()
        user_answer = data['answer'] if isinstance(data, dict) and 'answer' in data else ''
        
        if not user_answer:
            return jsonify({'error': '答案不能为空'}), 400
            
        # 让AI判断答案是否正确
        verify_prompt = f"""
请判断用户的答案是否正确。

正确答案：{correct_answer}
用户答案：{user_answer}

只需要回答"正确"或"错误"。
"""
        result = send_message(verify_prompt)
        is_correct = "正确" in result
        answer_attempts -= 1
        
        return jsonify({
            'status': 'success',
            'is_correct': is_correct,
            'remaining_attempts': answer_attempts,
            'message': '恭喜你答对了！' if is_correct else '答案不正确，请继续尝试' if answer_attempts > 0 else '已用完所有尝试机会'
        })

    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500

# 多人模式路由
@app.route('/multi/create_room', methods=['POST'])
@require_api_key
def create_room():
    try:
        data = get_request_data()
        print("Received data:", data)
        
        if not isinstance(data, dict):
            return jsonify({'error': '无效的请求数据格式'}), 400
            
        host_name = data.get('host_name')
        story_type = data.get('story_type')
        difficulty = data.get('difficulty')
        theme = data.get('theme')
        
        if not host_name:
            return jsonify({'error': '需要房主名称'}), 400

        room_id = str(uuid.uuid4())[:8]
        rooms[room_id] = Room(room_id, host_name, story_type, difficulty, theme)
        save_rooms()
        
        return jsonify({
            'status': 'success',
            'room_id': room_id,
            'host_name': host_name
        })
    except Exception as e:
        print(f"Error creating room: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/multi/join_room', methods=['POST'])
@require_api_key
def join_room():
    try:
        data = get_request_data()
        print("Join room request data:", data)  # 添加日志
        
        # 修改这里的数据获取方式
        if isinstance(data, dict):
            room_id = data.get('room_id')
            player_name = data.get('player_name')
        else:
            return jsonify({'error': '无效的请求数据格式'}), 400
        
        if not room_id or not player_name:
            return jsonify({'error': '需要房间ID和玩家名称'}), 400
            
        if room_id not in rooms:
            return jsonify({'error': '房间不存在'}), 404
            
        room = rooms[room_id]
        
        # 检查玩家名是否已存在
        if any(player['name'] == player_name for player in room.players):
            return jsonify({'error': '该玩家名已被使用'}), 400
            
        # 检查房间是否已满（最多8人）
        if len(room.players) >= 8:
            return jsonify({'error': '房间已满'}), 400
            
        # 检查游戏是否已经开始
        if room.status == 'playing':
            return jsonify({'error': '游戏已经开始'}), 400
            
        # 添加新玩家
        room.players.append({
            'name': player_name,
            'score': 0,
            'is_host': False
        })
        
        # 更新房间最后活动时间
        room.last_activity = time.time()
        
        # 更新答案尝试次数
        room.answer_attempts = len(room.players) * 2
        
        print(f"Player {player_name} joined room {room_id}")  # 添加日志
        
        return jsonify({
            'status': 'success',
            'message': '成功加入房间',
            'room_id': room_id,
            'players': room.players
        })
        
    except Exception as e:
        print(f"Error joining room: {str(e)}")  # 添加错误日志
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500

@app.route('/multi/start_game', methods=['POST'])
@require_api_key
def multi_start_game():
    try:
        data = get_request_data()
        print("Start game request data:", data)  # 添加调试日志
        
        if not isinstance(data, dict):
            return jsonify({'error': '无效的请求数据格式'}), 400
            
        room_id = data.get('room_id')
        player_name = data.get('player_name')
        
        if not room_id or not player_name:
            return jsonify({'error': '需要房间ID和玩家名称'}), 400
            
        room = rooms.get(room_id)
        if not room:
            return jsonify({'error': '房间不存在'}), 404
            
        # 检查是否是房主
        if room.host_name != player_name:
            return jsonify({'error': '只有房主可以开始游戏'}), 403
            
        # 检查玩家数量
        if len(room.players) < 2:
            return jsonify({'error': '至少需要2名玩家才能开始游戏'}), 400
            
        # 生成场景和答案
        room.status = 'playing'
        room.current_scenario = generate_scenario()  # 生成新场景
        room.current_player = 0
        room.question_count = 0
        room.hint_count = 2
        
        # 获取正确答案
        answer_prompt = f"基于场景：[{room.current_scenario}]，请详细解释这个谜题的答案。"
        room.correct_answer = send_message(answer_prompt)
        
        print(f"Generated scenario: {room.current_scenario}")  # 添加调试日志
        
        return jsonify({
            'status': 'success',
            'scenario': room.current_scenario,
            'hint_count': room.hint_count,
            'current_player': room.players[0]['name']
        })
        
    except Exception as e:
        print(f"Error starting game: {str(e)}")  # 添加错误日志
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500

def generate_scenario():
    prompt = """作为海龟汤游戏主持人，请直接输出一个谜题场景。
格式要求：必须以"场景：["开头，以"]"结尾。

注意：
1. 不要有任何其他文字说明
2. 不要解释规则
3. 只输出一个场景描述"""

    # 尝试最多3次获取有效场景
    for _ in range(3):
        scenario_text = send_message(prompt)
        start_index = scenario_text.find("场景：[")
        end_index = scenario_text.find("]", start_index)
        
        if start_index != -1 and end_index != -1:
            return scenario_text[start_index + 4:end_index]
            
    raise Exception("无法生成有效的场景，请重试")

@app.route('/multi/ask_question', methods=['POST'])
@require_api_key
def multi_ask_question():
    try:
        data = get_request_data()
        room_id = data['room_id'] if isinstance(data, dict) and 'room_id' in data else ''
        player_name = data['player_name'] if isinstance(data, dict) and 'player_name' in data else ''
        question = data['question'] if isinstance(data, dict) and 'question' in data else ''
        
        if not room_id or not player_name or not question:
            return jsonify({'error': '缺少必要参数'}), 400
            
        room = rooms.get(room_id)
        if not room:
            return jsonify({'error': '房间不存在'}), 404
            
        room.last_activity = time.time()
        
        if room.status != 'playing':
            return jsonify({'error': '游戏尚未开始'}), 400
            
        if room.players[room.current_player]['name'] != player_name:
            return jsonify({'error': '不是你的回合'}), 403

        prompt = f"基于场景：[{room.current_scenario}]，回答问题：{question}。只能回答'是'或'否'。"
        answer = send_message(prompt)
        room.question_count += 1
        
        # 轮到下一个玩家
        room.current_player = (room.current_player + 1) % len(room.players)
        
        return jsonify({
            'status': 'success',
            'answer': answer,
            'question_count': room.question_count,
            'next_player': room.players[room.current_player]['name']
        })

    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500

@app.route('/multi/request_hint', methods=['POST'])
@require_api_key
def multi_request_hint():
    try:
        data = get_request_data()
        room_id = data['room_id'] if isinstance(data, dict) and 'room_id' in data else ''
        player_name = data['player_name'] if isinstance(data, dict) and 'player_name' in data else ''
        
        if not room_id or not player_name:
            return jsonify({'error': '需要房间ID和玩家名称'}), 400
            
        room = rooms.get(room_id)
        if not room:
            return jsonify({'error': '房间不存在'}), 404
            
        room.last_activity = time.time()
        
        if room.status != 'playing':
            return jsonify({'error': '游戏尚未开始'}), 400
            
        if room.hint_count <= 0:
            return jsonify({'error': '没有剩余提示次数'}), 400
            
        prompt = f"基于场景：[{room.current_scenario}]，给出一个提示，帮助玩家理解情况，但不要直接透露答案。"
        hint = send_message(prompt)
        room.hint_count -= 1
        
        return jsonify({
            'status': 'success',
            'hint': hint,
            'remaining_hints': room.hint_count
        })

    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500

@app.route('/multi/get_room_status', methods=['POST'])
@require_api_key
def get_room_status():
    try:
        print("Received get_room_status request")  # 添加请求接收日志
        data = get_request_data()
        print("Request data:", data)  # 打印请求数据
        
        if not isinstance(data, dict):
            print("Invalid data format")  # 打印格式错误
            return jsonify({
                'status': 'error',
                'message': '无效的请求数据格式'
            }), 400
        
        room_id = data.get('room_id')
        print(f"Looking for room: {room_id}")  # 打印要查找的房间ID
        print(f"Available rooms: {list(rooms.keys())}")  # 打印可用房间列表
        
        if not room_id:
            print("No room_id provided")  # 打印缺少房间ID
            return jsonify({
                'status': 'error',
                'message': '需要房间ID'
            }), 400
            
        room = rooms.get(room_id)
        if not room:
            print(f"Room {room_id} not found")  # 打印房间未找到
            return jsonify({
                'status': 'error',
                'message': f'房间 {room_id} 不存在'
            }), 404
            
        room.last_activity = time.time()
        
        current_player_name = None
        if room.status == 'playing' and room.players:
            current_player_name = room.players[room.current_player]['name']
        
        # 添加调试日志
        print(f"Room {room_id} join requests:", room.join_requests)
        
        return jsonify({
            'status': 'success',
            'room_status': room.status,
            'players': room.players,
            'current_scenario': room.current_scenario,
            'question_count': room.question_count,
            'hint_count': room.hint_count,
            'current_player': current_player_name,
            'chat_messages': room.chat_messages,
            'join_requests': room.join_requests  # 确保返回加入请求列表
        })
    except Exception as e:
        print(f"Error getting room status: {str(e)}")  # 添加错误日志
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500

@app.route('/multi/check_answer', methods=['POST'])
@require_api_key
def multi_check_answer():
    try:
        data = get_request_data()
        room_id = data['room_id'] if isinstance(data, dict) and 'room_id' in data else ''
        player_name = data['player_name'] if isinstance(data, dict) and 'player_name' in data else ''
        user_answer = data['answer'] if isinstance(data, dict) and 'answer' in data else ''
        
        if not room_id or not player_name or not user_answer:
            return jsonify({'error': '缺少必要参数'}), 400
            
        room = rooms.get(room_id)
        if not room:
            return jsonify({'error': '房间不存在'}), 404
            
        if room.answer_attempts <= 0:
            return jsonify({'error': '已达到最大尝试次数'}), 400
            
        verify_prompt = f"""
请判断用户的答案是否正确。

正确答案：{room.correct_answer}
用户答案：{user_answer}

只需要回答"正确"或"错误"。
"""
        result = send_message(verify_prompt)
        is_correct = "正确" in result
        room.answer_attempts -= 1
        
        return jsonify({
            'status': 'success',
            'is_correct': is_correct,
            'remaining_attempts': room.answer_attempts,
            'message': '恭喜你答对了！' if is_correct else '答案不正确，请继续尝试' if room.answer_attempts > 0 else '已用完所有尝试机会'
        })

    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500

@app.route('/multi/send_voice', methods=['POST'])
@require_api_key
def send_voice():
    try:
        data = get_request_data()
        audio_file = room_id = data['audio'] if isinstance(data, dict) and 'audio' in data else '' # 使用 get() 方法而不是 []
        if not audio_file:
            return jsonify({'error': '没有收到音频文件'}), 400
            
        room_id = data['room_id'] if isinstance(data, dict) and 'room_id' in data else ''
        player_name = data['player_name'] if isinstance(data, dict) and 'player_name' in data else ''
        
        if not room_id or not player_name:
            return jsonify({'error': '缺少必要参数'}), 400
            
        if room_id not in rooms:
            return jsonify({'error': '房间不存在'}), 404
            
        # 这里可以添加保存或转发语音消息的逻辑
        
        return jsonify({
            'status': 'success',
            'message': '语音消息已发送'
        })
        
    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500

def send_message(prompt):
    try:
        request_data = {
            "header": {
                "app_id": SPARKAI_APP_ID,
                "uid": "12345"
            },
            "parameter": {
                "chat": {
                    "domain": SPARKAI_DOMAIN,
                    "temperature": 0.7,
                    "max_tokens": 2048
                }
            },
            "payload": {
                "message": {
                    "text": [
                        {
                            "role": "user",
                            "content": prompt
                        }
                    ]
                }
            }
        }

        print("发送请求数据:", json.dumps(request_data, ensure_ascii=False))
        
        ws = websocket.WebSocket(sslopt={"cert_reqs": ssl.CERT_NONE})
        ws.connect(get_spark_auth_url())
        
        ws.send(json.dumps(request_data))
        
        # 循环接收完整响应
        full_response = ""
        while True:
            try:
                response = ws.recv()
                if not response:
                    break
                    
                response_data = json.loads(response)
                if response_data["header"]["code"] != 0:
                    raise Exception(f"API错误: {response_data['header'].get('message', '未知错误')}")
                
                content = response_data["payload"]["choices"]["text"][0]["content"]
                full_response += content
                
                # 检查是否是最后一条消息
                if response_data["payload"]["choices"]["status"] == 2:
                    break
            except websocket.WebSocketConnectionClosedException:
                break
                
        ws.close()
        
        print("收到完整响应:", full_response)
        return full_response

    except Exception as e:
        print(f"send_message 发生错误: {str(e)}")
        raise

# 添加获取分类信息的路由
@app.route('/get_categories', methods=['GET'])
def get_categories():
    return jsonify(STORY_CATEGORIES)

@app.route('/multi/send_message', methods=['POST'])
@require_api_key
def send_chat_message():
    try:
        data = get_request_data()
        if not isinstance(data, dict):
            return jsonify({'error': '无效的请求数据格式'}), 400
            
        room_id = data.get('room_id')
        player_name = data.get('player_name')
        message = data.get('message')
        
        if not all([room_id, player_name, message]):
            return jsonify({'error': '缺少必要参数'}), 400
            
        room = rooms.get(room_id)
        if not room:
            return jsonify({'error': '房间不存在'}), 404
            
        # 添加新消息
        new_message = {
            'sender': player_name,
            'content': message,
            'time': time.strftime('%H:%M:%S')
        }
        room.chat_messages.append(new_message)
        
        # 只保留最近的100条消息
        if len(room.chat_messages) > 100:
            room.chat_messages = room.chat_messages[-100:]
            
        return jsonify({
            'status': 'success',
            'message': new_message
        })
        
    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500

# 修改获取房间列表的路由
@app.route('/multi/get_rooms', methods=['POST'])  # 改为 POST 方法
@require_api_key
def get_rooms():
    try:
        room_list = []
        print(f"Current rooms: {list(rooms.keys())}")  # 添加调试日志
        
        for room_id, room in rooms.items():
            room_list.append({
                'room_id': room_id,
                'host_name': room.host_name,
                'player_count': len(room.players),
                'status': room.status,
                'story_type': room.story_type,
                'difficulty': room.difficulty,
                'theme': room.theme
            })
            
        return jsonify({
            'status': 'success',
            'rooms': room_list
        })
    except Exception as e:
        print(f"Error getting rooms list: {str(e)}")  # 添加错误日志
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500

# 添加申请加入房间的路由
@app.route('/multi/request_join', methods=['POST'])
@require_api_key
def request_join():
    try:
        data = get_request_data()
        room_id = data['room_id'] if isinstance(data, dict) and 'room_id' in data else ''
        player_name = data['player_name'] if isinstance(data, dict) and 'player_name' in data else ''
        
        if not room_id or not player_name:
            return jsonify({'error': '缺少必要参数'}), 400
            
        room = rooms.get(room_id)
        if not room:
            return jsonify({'error': '房间不存在'}), 404
            
        if any(p['name'] == player_name for p in room.players):
            return jsonify({'error': '玩家已在房间中'}), 400
            
        # 检查是否已经有待处理的请求
        if any(req['name'] == player_name for req in room.join_requests):
            return jsonify({'error': '已发送过加入请求'}), 400
            
        # 添加加入请求
        room.join_requests.append({
            'name': player_name,
            'time': time.time()
        })
        save_rooms()
        
        return jsonify({
            'status': 'success',
            'message': '已发送加入请求，等待房主审核'
        })
    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500

# 添加处理加入请求的路由
@app.route('/multi/handle_join_request', methods=['POST'])
@require_api_key
def handle_join_request():
    try:
        data = get_request_data()
        print("Handle join request data:", data)  # 添加调试日志
        
        room_id = data['room_id'] if isinstance(data, dict) and 'room_id' in data else ''
        host_name = data['host_name'] if isinstance(data, dict) and 'host_name' in data else ''
        player_name = data['player_name'] if isinstance(data, dict) and 'player_name' in data else ''
        accept = data['accept'] if isinstance(data, dict) and 'accept' in data else ''
        
        print(f"Processing request - Room: {room_id}, Host: {host_name}, Player: {player_name}")  # 调试日志
        
        if not all([room_id, host_name, player_name]):
            return jsonify({'error': '缺少必要参数'}), 400
            
        room = rooms.get(room_id)
        if not room:
            return jsonify({'error': '房间不存在'}), 404
            
        print(f"Room host: {room.host_name}, Request host: {host_name}")  # 调试日志
        
        # 修改这里：使用当前房主名称而不是请求中的名称
        if room.host_name != host_name:
            return jsonify({
                'status': 'error',
                'message': '只有房主可以处理加入请求'
            }), 403
            
        # 查找并处理加入请求
        request_index = next((i for i, req in enumerate(room.join_requests) 
                            if req['name'] == player_name), -1)
                            
        if request_index == -1:
            return jsonify({'error': '未找到该加入请求'}), 404
            
        # 移除请求
        room.join_requests.pop(request_index)
        
        if accept:
            # 添加新玩家
            room.players.append({
                'name': player_name,
                'score': 0,
                'is_host': False
            })
            message = f'已接受 {player_name} 的加入请求'
        else:
            message = f'已拒绝 {player_name} 的加入请求'
            
        save_rooms()
        
        return jsonify({
            'status': 'success',
            'message': message
        })
    except Exception as e:
        print(f"Error handling join request: {str(e)}")  # 添加错误日志
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500

@app.route('/')
def index():
    return send_from_directory('.', 'index.html')

# 添加错误处理
@app.errorhandler(Exception)
def handle_error(error):
    print(f"Error: {str(error)}")  # 添加日志
    return jsonify({
        'status': 'error',
        'message': str(error)
    }), 500

if __name__ == '__main__':
    load_rooms()  # 加载房间数据
    app.run(host='0.0.0.0', port=8000, debug=True)  # 开启调试模式