# src/plugins/world_war_plugin/plugin.py
"""世界大战模拟器插件 (World War Plugin)
允许用户加入国家、战斗、结盟、拥有军衔和领土。游戏数据全局共享，存储在 game_data.json 文件中。
包含公屏战报和随机事件系统。

此插件使用GPL v3.0版本的许可证，作者：Unreal and 何夕。改编时请保留此声明
"""
import os
import json
import random
import time
import re
from typing import Dict, Any, Set, Optional, List, Tuple, Union, Type

# MaiBot 核心导入
from src.plugin_system import (
    BasePlugin, register_plugin, ConfigField,
    BaseCommand, BaseAction, ComponentInfo, ChatMode, ActionActivationType,
    PythonDependency
)
# APIs 导入
from src.plugin_system.apis import chat_api, send_api

# - 军衔定义 -
RANKS = [
    {"name": "元帅", "level": 11},
    # 将官
    {"name": "上将", "level": 10},
    {"name": "中将", "level": 9},
    {"name": "少将", "level": 8},
    # 校官
    {"name": "大校", "level": 7},
    {"name": "上校", "level": 6},
    {"name": "中校", "level": 5},
    {"name": "少校", "level": 4},
    # 尉官
    {"name": "上尉", "level": 3},
    {"name": "中尉", "level": 2},
    {"name": "少尉", "level": 1},
]
RANK_NAMES = [r["name"] for r in RANKS]
RANK_LEVELS = {r["name"]: r["level"] for r in RANKS}

# - 国家制度定义 -
IDEOLOGIES = ["民主", "共和", "君主", "社会主义", "资本主义", "军国主义", "无政府主义", "联邦", "独裁", "人民代表大会", "FXS"]
IDEOLOGY_EFFECTS = {
    "民主": "提高国民幸福度，但可能降低战争效率。",
    "共和": "平衡发展各项指标。",
    "君主": "稳定，但发展速度较慢。",
    "社会主义": "资源分配平均，但可能抑制个人积极性。",
    "资本主义": "经济发展快，但贫富差距可能加大。",
    "军国主义": "军事力量强大，但民生可能被忽视。",
    "无政府主义": "自由度极高，但难以形成有效组织。",
    "联邦": "地方自治，中央协调，但决策可能较慢。",
    "独裁": "集中力量办大事，但可能压制异议。",
    "人民代表大会": "代表民意，但效率可能受程序影响。",
    "FXS": "极端政治立场。"
}

# - 随机事件定义 -
RANDOM_EVENTS = [
    {"name": "丰收之年", "effect": "nation.troops", "multiplier": 1.1, "description": "今年风调雨顺，军队士气高昂，战斗力提升了10%。"},
    {"name": "瘟疫流行", "effect": "nation.troops", "multiplier": 0.9, "description": "一场瘟疫席卷全国，军队减员严重，战斗力下降了10%。"},
    {"name": "技术突破", "effect": "nation.troops", "multiplier": 1.15, "description": "科学家们取得了重大突破，新式武器装备提升了军队15%的战斗力。"},
    {"name": "经济危机", "effect": "nation.troops", "multiplier": 0.85, "description": "经济不景气，军费削减，军队战斗力下降了15%。"},
    {"name": "领土扩张", "effect": "nation.territory", "multiplier": 1.05, "description": "勘探队发现了新土地，国家领土增加了5%。"},
    {"name": "自然灾害", "effect": "nation.territory", "multiplier": 0.95, "description": "地震或洪水摧毁了部分领土，国家领土减少了5%。"},
    {"name": "人口增长", "effect": "nation.population", "multiplier": 1.1, "description": "移民潮涌入，国家人口增长了10%。"},
    {"name": "人口减少", "effect": "nation.population", "multiplier": 0.9, "description": "战争或疾病导致人口减少，国家人口下降了10%。"},
]

# - 静态辅助方法 -
def get_player_info(game_state: Dict[str, Any], user_id: str) -> Optional[Dict[str, Any]]:
    """获取玩家信息"""
    return game_state.get("players", {}).get(user_id)

def get_player_nation(game_state: Dict[str, Any], user_id: str) -> Optional[str]:
    """根据用户ID获取其所属国家名称"""
    player_info = get_player_info(game_state, user_id)
    return player_info.get("nation") if player_info else None

def get_nation_info(game_state: Dict[str, Any], nation_name: str) -> Optional[Dict[str, Any]]:
    """根据国家名称获取国家信息"""
    return game_state.get("nations", {}).get(nation_name)

def are_allies(game_state: Dict[str, Any], nation1: str, nation2: str) -> bool:
    """检查两个国家是否是盟友"""
    alliances = game_state.get("alliances", set())
    if not isinstance(alliances, set):
        # 兼容旧数据格式（列表）
        alliances = set(frozenset(a) for a in alliances) if isinstance(alliances, list) else set()

    for alliance in alliances:
        if isinstance(alliance, (list, tuple, set, frozenset)):
             if nation1 in alliance and nation2 in alliance:
                 return True
    return False

def format_player_info(user_id: str, player_info: Optional[Dict[str, Any]], nation_info: Optional[Dict[str, Any]]) -> str:
    """格式化玩家信息字符串"""
    if not player_info:
        return "你尚未加入任何国家。请使用 /join <国家名> 加入。"
    deployed_str = f" (驻扎 {nation_info.get('deployed_troops', 0)} 兵力)" if nation_info and nation_info.get('deployed_troops', 0) > 0 else ""
    total_troops_str = f" (国家总兵力: {nation_info['troops']})" if nation_info else ""
    return (
        f"👤 玩家ID: {user_id}\n"
        f"🎖️ 军衔: {player_info.get('rank', '士兵')}\n"
        f"🌍 国家: {player_info['nation']}{deployed_str}{total_troops_str}"
    )

def get_allies(game_state: Dict[str, Any], player_nation: str) -> List[str]:
    """获取玩家国家的所有盟友"""
    allies = []
    alliances = game_state.get("alliances", set())
    if not isinstance(alliances, set):
        # 兼容旧数据格式（列表）
        alliances = set(frozenset(a) for a in alliances) if isinstance(alliances, list) else set()

    for alliance in alliances:
        if isinstance(alliance, (list, tuple, set, frozenset)):
            if player_nation in alliance:
                allies.extend([n for n in alliance if n != player_nation])
    return allies

def format_help_menu() -> str:
    """格式化帮助菜单字符串"""
    help_text = (
        "🌍 世界大战模拟器 帮助菜单 🌍\n"
        "可用命令列表：\n"
        "/join <国家名> - 加入或创建一个国家\n"
        "/my - 查看自己的信息\n"
        "/friends - 查看当前的友军列表\n"
        "/pvp <国家名> <出兵数量> - 对指定国家发起战斗\n"
        "/conquer <国家名> <出兵数量> - 对指定国家发起掠夺领土\n"
        "/ally <国家名> - 与指定国家结盟\n"
        "/withdraw <国家名> - 解除与指定国家的盟友关系\n"
        "/deploy <数量> - 在自己的领土上驻军 (正数部署，负数撤回)\n"
        "/transfer <用户ID> <兵力数量> - (领袖) 将国家兵力转移给同国玩家\n"
        "/appoint <用户ID> <军衔> - (领袖) 任命官职\n"
        "/set_ideology <制度> - (领袖) 设置国家制度\n"
        "/nation - 查看自己国家的信息\n"
        "/world - 查看世界现状\n"
        "/help - 显示此帮助菜单\n"
        "提示：战斗和掠夺时，防守方的有效兵力是其国家总兵力减去驻军数量。"
    )
    return help_text

# 计算 Elo 等级分变化
def calculate_elo_change(rating_a: float, rating_b: float, score_a: float, k_factor: float = 32.0) -> Tuple[float, float]:
    """计算 Elo 等级分变化"""
    expected_score_a = 1 / (1 + 10 ** ((rating_b - rating_a) / 400))
    expected_score_b = 1 / (1 + 10 ** ((rating_a - rating_b) / 400))
    actual_score_a = score_a
    actual_score_b = 1 - score_a
    delta_a = k_factor * (actual_score_a - expected_score_a)
    delta_b = k_factor * (actual_score_b - expected_score_b)
    return delta_a, delta_b

# --- 隐蔽的违禁词列表 ---
# 请根据实际情况扩展此列表
# 变量名经过混淆以增加隐蔽性
# --- 声明：本列表只能扩展，如修改或删除导致的后果，原作者不予承担责任
_b_w_l_ = {
    "作弊", "外挂", "开挂", "hack", "cheat", 
    "fuck", "shit", "damn", "asshole", 
    "草泥马", "法克", "傻逼", "白痴", "尼玛", "滚", "去死", 
    "希特勒", "近平", "泽东", "法西斯", "台独", "港独", "香港", "台湾", "澳门", "赌场",
    "纳粹", "卐"
}
# --- 违禁词列表结束 ---

# --- 违禁词检测函数 ---
def contains_banned_words(text: str, banned_words: Set[str] = _b_w_l_) -> bool:
    """
    检查文本中是否包含违禁词。
    Args:
        text (str): 要检查的文本。
        banned_words (Set[str]): 违禁词集合 (默认使用 _b_w_l_)。
    Returns:
        bool: 如果包含违禁词返回 True，否则返回 False。
    """
    if not banned_words:
        return False
    # 转义特殊字符并组合成一个正则模式，进行忽略大小写的完整单词匹配
    escaped_words = [re.escape(word) for word in banned_words]
    pattern = r'\b(?:' + '|'.join(escaped_words) + r')\b'
    return bool(re.search(pattern, text, re.IGNORECASE))
# --- 违禁词检测函数结束 ---

# - 插件主类 -
@register_plugin
class WorldWarPlugin(BasePlugin):
    """世界大战模拟器插件"""
    plugin_name = "world_war_plugin"
    plugin_description = "模拟世界大战，玩家可以加入国家、战斗、结盟、拥有军衔和领土。包含公屏战报和随机事件。"
    plugin_version = "1.1.0" # 更新版本
    config_file_name = "config.toml" # 明确指定配置文件名

    def __init__(self, *args, **kwargs):
        """
        初始化插件实例。
        接受任意位置参数和关键字参数，以兼容 MaiBot 框架的初始化调用。
        """
        # 调用父类 BasePlugin 的 __init__ 方法，处理框架传递的参数（如 plugin_dir 等）
        super().__init__(*args, **kwargs)
        
        # 定义游戏数据文件路径 (使用相对路径)
        self.data_file = "./World/data/game_data.json" 
        # 初始化游戏状态 (在 on_load 时会从文件加载)
        self.game_state: Optional[Dict[str, Any]] = None

    # --- 实现抽象基类要求的方法 ---
    def enable_plugin(self, enabled: Optional[bool] = None) -> bool:
        """
        实现 BasePlugin 的抽象方法。
        报告插件的启用状态，并可选择性地尝试设置它。
        插件的实际启用状态由配置文件 config.toml 中的 [plugin].enabled 控制。
        """
        current_status = self.get_config("plugin.enabled", True)
        if enabled is not None and enabled != current_status:
            self.logger.warning(
                f"插件启用状态应通过配置文件 '{self.config_file_name}' 中的 [plugin].enabled 项控制。"
                f"尝试通过代码设置为 {enabled} 的操作将被忽略。"
            )
        return current_status

    @property
    def config_schema(self) -> Dict[str, Any]:
        """定义插件配置结构"""
        return {
            "plugin": {
                "enabled": ConfigField(type=bool, default=True, description="是否启用插件"),
                "config_version": ConfigField(type=str, default="1.1.0", description="配置文件版本"),
            },
            "game": {
                "initial_troops": ConfigField(type=int, default=1000, description="初始兵力"),
                "initial_territory": ConfigField(type=int, default=15, description="初始领土"),
                "initial_population": ConfigField(type=int, default=1000000, description="初始人口"),
                "elo_k_factor": ConfigField(type=float, default=32.0, description="Elo 计算 K 因子"),
                "enable_public_announcements": ConfigField(type=bool, default=True, description="是否启用公屏公告"),
                "announcement_chat_id": ConfigField(type=str, default="12345678", description="公屏公告发送到的群ID"),
                "event_interval_min": ConfigField(type=int, default=60, description="随机事件最小间隔（分钟）"),
                "event_interval_max": ConfigField(type=int, default=120, description="随机事件最大间隔（分钟）"),
            },
            "components": {
                "enable_join_command": ConfigField(type=bool, default=True, description="是否启用加入命令"),
                "enable_my_command": ConfigField(type=bool, default=True, description="是否启用查看信息命令"),
                "enable_friends_command": ConfigField(type=bool, default=True, description="是否启用查看盟友命令"),
                "enable_pvp_command": ConfigField(type=bool, default=True, description="是否启用战斗命令"),
                "enable_conquer_command": ConfigField(type=bool, default=True, description="是否启用掠夺命令"),
                "enable_ally_command": ConfigField(type=bool, default=True, description="是否启用结盟命令"),
                "enable_withdraw_command": ConfigField(type=bool, default=True, description="是否启用解除盟约命令"),
                "enable_deploy_command": ConfigField(type=bool, default=True, description="是否启用驻军命令"),
                "enable_transfer_command": ConfigField(type=bool, default=True, description="是否启用兵力转移命令"),
                "enable_appoint_command": ConfigField(type=bool, default=True, description="是否启用任命命令"),
                "enable_set_ideology_command": ConfigField(type=bool, default=True, description="是否启用设置制度命令"),
                "enable_help_command": ConfigField(type=bool, default=True, description="是否启用帮助命令"),
                "enable_nation_command": ConfigField(type=bool, default=True, description="是否启用国家信息命令"),
                "enable_world_command": ConfigField(type=bool, default=True, description="是否启用世界现状命令"), # 新增配置项
                "enable_random_event_action": ConfigField(type=bool, default=True, description="是否启用随机事件 Action"),
            }
        }

    @property
    def python_dependencies(self) -> list:
        return []

    @property
    def dependencies(self) -> list:
        return []

    def get_plugin_components(self) -> List[Tuple[ComponentInfo, Type]]:
        """返回插件包含的所有组件"""
        components = []
        if self.get_config("components.enable_join_command", True):
            components.append((self.JoinCommand.get_command_info(), self.JoinCommand))
        if self.get_config("components.enable_my_command", True):
            components.append((self.MyCommand.get_command_info(), self.MyCommand))
        if self.get_config("components.enable_friends_command", True):
            components.append((self.FriendsCommand.get_command_info(), self.FriendsCommand))
        if self.get_config("components.enable_pvp_command", True):
            components.append((self.PvpCommand.get_command_info(), self.PvpCommand))
        if self.get_config("components.enable_conquer_command", True):
            components.append((self.ConquerCommand.get_command_info(), self.ConquerCommand))
        if self.get_config("components.enable_ally_command", True):
            components.append((self.AllyCommand.get_command_info(), self.AllyCommand))
        if self.get_config("components.enable_withdraw_command", True):
            components.append((self.WithdrawCommand.get_command_info(), self.WithdrawCommand))
        if self.get_config("components.enable_deploy_command", True):
            components.append((self.DeployCommand.get_command_info(), self.DeployCommand))
        if self.get_config("components.enable_transfer_command", True):
            components.append((self.TransferCommand.get_command_info(), self.TransferCommand))
        if self.get_config("components.enable_appoint_command", True):
            components.append((self.AppointCommand.get_command_info(), self.AppointCommand))
        if self.get_config("components.enable_set_ideology_command", True):
            components.append((self.SetIdeologyCommand.get_command_info(), self.SetIdeologyCommand))
        if self.get_config("components.enable_help_command", True):
            components.append((self.HelpCommand.get_command_info(), self.HelpCommand))
        if self.get_config("components.enable_nation_command", True):
            components.append((self.NationCommand.get_command_info(), self.NationCommand))
        # --- 注册 WorldCommand ---
        if self.get_config("components.enable_world_command", True):
            components.append((self.WorldCommand.get_command_info(), self.WorldCommand))
        # --- 注册结束 ---

        # Action 组件 (包括随机事件)
        if self.get_config("components.enable_random_event_action", True):
            components.append((self.RandomEventAction.get_action_info(), self.RandomEventAction))

        return components

    @staticmethod
    def _load_game_data(data_file_path: str) -> Dict[str, Any]:
        """从JSON文件加载游戏数据"""
        default_data = {"players": {}, "nations": {}, "alliances": set(), "last_event_time": 0}
        if os.path.exists(data_file_path):
            try:
                with open(data_file_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                data.setdefault("players", {})
                data.setdefault("nations", {})
                data.setdefault("last_event_time", 0)
                raw_alliances = data.setdefault("alliances", [])
                if isinstance(raw_alliances, list) and raw_alliances and isinstance(raw_alliances[0], list):
                    data["alliances"] = set(frozenset(a) for a in raw_alliances)
                elif isinstance(raw_alliances, list):
                    data["alliances"] = set()
                else:
                    data["alliances"] = set()
                return data
            except Exception as e:
                # 假设可以从全局或某个地方获取 logger，这里简化处理
                print(f"[WorldWarPlugin] 加载游戏数据失败: {e}") 
                return default_data
        else:
            # 文件不存在，返回默认数据
            return default_data

    @staticmethod
    def _save_game_data(data_to_save: Dict[str, Any], data_file_path: str):
        """将游戏数据保存到JSON文件"""
        if data_to_save is None:
             print("尝试保存游戏数据，但数据为空。")
             return
        try:
            serializable_state = data_to_save.copy()
            if "alliances" in serializable_state and isinstance(serializable_state["alliances"], set):
                serializable_state["alliances"] = [list(a) for a in data_to_save["alliances"]]
            with open(data_file_path, 'w', encoding='utf-8') as f:
                json.dump(serializable_state, f, ensure_ascii=False, indent=4)
        except Exception as e:
            print(f"保存游戏数据失败: {e}") # 简化处理

    @staticmethod
    async def broadcast_to_public_static(message: str, target_chat_id: str, enable_announcements: bool):
        """静态方法：广播消息到公屏"""
        if not enable_announcements:
            print("公屏公告已禁用，跳过广播。")
            return

        try:
            # 使用 send_api 发送消息到指定群聊
            await send_api.send_to_chat_stream(target_chat_id, message)
            print(f"已广播到公屏 ({target_chat_id}): {message}")
        except Exception as e:
             print(f"广播到公屏失败: {e}")

    async def on_load(self):
        """插件加载时执行"""
        print("世界大战插件已加载。")
        # 在插件加载时初始化 game_state
        self.game_state = self._load_game_data(self.data_file)

    async def on_unload(self):
        """插件卸载时执行"""
        print("世界大战插件正在卸载，保存游戏数据...")
        self._save_game_data(self.game_state, self.data_file)
        print("世界大战插件已卸载。")

    # --- Command 组件 ---

    class JoinCommand(BaseCommand):
        command_name = "join_command"
        command_description = "加入或创建一个国家"
        command_pattern = r"^/join\s+(.+)$"

        async def execute(self) -> tuple[bool, str | None, bool]:
            try:
                user_id = self.message.message_info.user_info.user_id
                raw_message = self.message.raw_message
            except AttributeError as e:
                error_msg = "无法获取用户信息或消息内容。"
                await self.send_text(error_msg)
                return False, error_msg, True

            data_file_path = "./World/data/game_data.json"
            try:
                game_data = WorldWarPlugin._load_game_data(data_file_path)
            except Exception as e:
                error_msg = f"加载游戏数据失败: {e}"
                await self.send_text(error_msg)
                return False, error_msg, True

            match = re.match(self.command_pattern, raw_message)
            if not match:
                error_msg = "命令格式错误。请使用: /join <国家名>"
                await self.send_text(error_msg)
                return False, error_msg, True

            nation_name = match.group(1).strip()

            # --- 添加违禁词检查 ---
            if contains_banned_words(nation_name):
                msg = "❌ 国家名称包含不适当的内容，请重新输入。"
                await self.send_text(msg)
                return True, msg, True # 成功处理请求，但拦截消息
            # --- 违禁词检查结束 ---

            player_info = get_player_info(game_data, user_id)

            if player_info and player_info.get("nation") == nation_name:
                msg = f"你已经是 {nation_name} 的成员了。"
                await self.send_text(msg)
                return True, msg, True

            if player_info and player_info.get("nation") != nation_name:
                old_nation = player_info["nation"]
                old_nation_info = get_nation_info(game_data, old_nation)
                if old_nation_info:
                    old_nation_info["members"] = [m for m in old_nation_info.get("members", []) if m != user_id]
                    if old_nation_info.get("leader") == user_id:
                        old_nation_info["leader"] = None

            existing_nation = get_nation_info(game_data, nation_name)
            if not existing_nation:
                # 直接通过 self.get_config 访问配置
                new_nation = {
                    "name": nation_name,
                    "troops": self.get_config("game.initial_troops", 1000),
                    "territory": self.get_config("game.initial_territory", 15),
                    "population": self.get_config("game.initial_population", 1000000),
                    "elo": 1500,
                    "leader": user_id,
                    "members": [user_id],
                    "ideology": random.choice(IDEOLOGIES),
                    "deployed_troops": 0
                }
                game_data["nations"][nation_name] = new_nation
                msg = f"🎉 恭喜！你创建了新的国家 {nation_name} 并成为领袖！\n制度: {new_nation['ideology']}\n{IDEOLOGY_EFFECTS.get(new_nation['ideology'], '')}"
                # 调用静态广播方法
                await WorldWarPlugin.broadcast_to_public_static(
                    f"🌍 全球新闻: 玩家 {user_id} 创建了新国家 {nation_name}！",
                    self.get_config("game.announcement_chat_id", "12345678"),
                    self.get_config("game.enable_public_announcements", True)
                )
            else:
                if existing_nation.get("leader") and user_id in existing_nation.get("members", []):
                     msg = f"你已经是 {nation_name} 的成员了。"
                     await self.send_text(msg)
                     return True, msg, True
                existing_nation.setdefault("members", []).append(user_id)
                if not existing_nation.get("leader"):
                    existing_nation["leader"] = user_id
                msg = f"🎉 欢迎加入 {nation_name}！"
                # 调用静态广播方法
                await WorldWarPlugin.broadcast_to_public_static(
                    f"🌍 全球新闻: 玩家 {user_id} 加入了国家 {nation_name}！",
                    self.get_config("game.announcement_chat_id", "12345678"),
                    self.get_config("game.enable_public_announcements", True)
                )

            game_data["players"][user_id] = {
                "user_id": user_id,
                "nation": nation_name,
                "rank": "士兵"
            }

            try:
                WorldWarPlugin._save_game_data(game_data, data_file_path)
            except Exception as e:
                error_msg = f"保存游戏数据失败: {e}"
                await self.send_text(error_msg)
                return False, error_msg, True

            await self.send_text(msg)
            return True, msg, True

    class MyCommand(BaseCommand):
        command_name = "my_command"
        command_description = "查看自己的信息"
        command_pattern = r"^/my$"

        async def execute(self) -> tuple[bool, str | None, bool]:
            try:
                user_id = self.message.message_info.user_info.user_id
            except AttributeError as e:
                error_msg = "无法获取用户信息。"
                await self.send_text(error_msg)
                return False, error_msg, True

            data_file_path = "./World/data/game_data.json"
            try:
                game_data = WorldWarPlugin._load_game_data(data_file_path)
            except Exception as e:
                error_msg = f"加载游戏数据失败: {e}"
                await self.send_text(error_msg)
                return False, error_msg, True

            player_info = get_player_info(game_data, user_id)
            player_nation_name = get_player_nation(game_data, user_id)
            nation_info = get_nation_info(game_data, player_nation_name) if player_nation_name else None

            info_str = format_player_info(user_id, player_info, nation_info)
            await self.send_text(info_str)
            return True, info_str, True

    class FriendsCommand(BaseCommand):
        command_name = "friends_command"
        command_description = "查看当前的友军列表"
        command_pattern = r"^/friends$"

        async def execute(self) -> tuple[bool, str| None, bool]:
            try:
                user_id = self.message.message_info.user_info.user_id
            except AttributeError as e:
                error_msg = "无法获取用户信息。"
                await self.send_text(error_msg)
                return False, error_msg, True

            data_file_path = "./World/data/game_data.json"
            try:
                game_data = WorldWarPlugin._load_game_data(data_file_path)
            except Exception as e:
                error_msg = f"加载游戏数据失败: {e}"
                await self.send_text(error_msg)
                return False, error_msg, True

            player_nation_name = get_player_nation(game_data, user_id)
            if not player_nation_name:
                msg = "你尚未加入任何国家。"
                await self.send_text(msg)
                return True, msg, True

            allies = get_allies(game_data, player_nation_name)
            if not allies:
                msg = "你的国家目前没有盟友。使用 /ally <国家名> 来结盟。"
            else:
                msg = f"🤝 你的国家 {player_nation_name} 的盟友列表:\n" + "\n".join(allies)
            await self.send_text(msg)
            return True, msg, True

    class PvpCommand(BaseCommand):
        command_name = "pvp_command"
        command_description = "对指定国家发起战斗"
        command_pattern = r"^/pvp\s+(.+?)\s+(\d+)$"

        async def execute(self) -> tuple[bool, str | None, bool]:
            try:
                user_id = self.message.message_info.user_info.user_id
                raw_message = self.message.raw_message
            except AttributeError as e:
                error_msg = "无法获取用户信息或消息内容。"
                await self.send_text(error_msg)
                return False, error_msg, True

            data_file_path = "./World/data/game_data.json"
            try:
                game_data = WorldWarPlugin._load_game_data(data_file_path)
            except Exception as e:
                error_msg = f"加载游戏数据失败: {e}"
                await self.send_text(error_msg)
                return False, error_msg, True

            match = re.match(self.command_pattern, raw_message)
            if not match:
                error_msg = "命令格式错误。请使用: /pvp <国家名> <出兵数量>"
                await self.send_text(error_msg)
                return False, error_msg, True

            target_nation_name = match.group(1).strip()
            try:
                attack_troops = int(match.group(2))
            except ValueError:
                error_msg = "出兵数量必须是一个整数。"
                await self.send_text(error_msg)
                return False, error_msg, True

            attacker_nation_name = get_player_nation(game_data, user_id)
            if not attacker_nation_name:
                msg = "你尚未加入任何国家，无法发起战斗。请先使用 /join <国家名>。"
                await self.send_text(msg)
                return True, msg, True

            attacker_nation_info = get_nation_info(game_data, attacker_nation_name)
            target_nation_info = get_nation_info(game_data, target_nation_name)

            if not target_nation_info:
                msg = f"国家 {target_nation_name} 不存在。"
                await self.send_text(msg)
                return True, msg, True

            if attacker_nation_name == target_nation_name:
                msg = "你不能攻击自己的国家。"
                await self.send_text(msg)
                return True, msg, True

            if are_allies(game_data, attacker_nation_name, target_nation_name):
                msg = f"你的国家 {attacker_nation_name} 与 {target_nation_name} 是盟友，无法发起攻击。"
                await self.send_text(msg)
                return True, msg, True

            if attack_troops <= 0:
                msg = "出兵数量必须大于0。"
                await self.send_text(msg)
                return True, msg, True

            if attacker_nation_info["troops"] < attack_troops:
                msg = f"你的国家兵力不足。当前兵力: {attacker_nation_info['troops']}。"
                await self.send_text(msg)
                return True, msg, True

            effective_defense_troops = max(1, target_nation_info["troops"] - target_nation_info.get("deployed_troops", 0))
            attack_power = attack_troops * random.uniform(0.8, 1.2)
            defense_power = effective_defense_troops * random.uniform(0.8, 1.2)

            if attack_power > defense_power:
                damage_dealt = min(int(attack_troops * 0.3), target_nation_info["troops"])
                damage_taken = min(int(effective_defense_troops * 0.2), attack_troops)

                attacker_nation_info["troops"] -= damage_taken
                target_nation_info["troops"] -= damage_dealt

                # 直接通过 self.get_config 访问配置
                attacker_elo_change, target_elo_change = calculate_elo_change(
                    attacker_nation_info.get("elo", 1500),
                    target_nation_info.get("elo", 1500),
                    1.0,
                    self.get_config("game.elo_k_factor", 32.0)
                )
                attacker_nation_info["elo"] = max(100, attacker_nation_info.get("elo", 1500) + attacker_elo_change)
                target_nation_info["elo"] = max(100, target_nation_info.get("elo", 1500) + target_elo_change)

                result_msg = (
                    f"⚔️ 战斗结果！\n"
                    f"国家 {attacker_nation_name} 攻击 {target_nation_name} 获胜！\n"
                    f"{attacker_nation_name} 损失 {damage_taken} 兵力，剩余 {attacker_nation_info['troops']}。\n"
                    f"{target_nation_name} 损失 {damage_dealt} 兵力，剩余 {target_nation_info['troops']}。"
                )
                await self.send_text(result_msg)
                # 调用静态广播方法
                await WorldWarPlugin.broadcast_to_public_static(
                    result_msg,
                    self.get_config("game.announcement_chat_id", "12345678"),
                    self.get_config("game.enable_public_announcements", True)
                )

            else:
                damage_dealt = min(int(effective_defense_troops * 0.3), attack_troops)
                damage_taken = min(int(attack_troops * 0.2), effective_defense_troops)

                attacker_nation_info["troops"] -= damage_dealt
                target_nation_info["troops"] -= damage_taken

                # 直接通过 self.get_config 访问配置
                attacker_elo_change, target_elo_change = calculate_elo_change(
                    attacker_nation_info.get("elo", 1500),
                    target_nation_info.get("elo", 1500),
                    0.0,
                    self.get_config("game.elo_k_factor", 32.0)
                )
                attacker_nation_info["elo"] = max(100, attacker_nation_info.get("elo", 1500) + attacker_elo_change)
                target_nation_info["elo"] = max(100, target_nation_info.get("elo", 1500) + target_elo_change)

                result_msg = (
                    f"⚔️ 战斗结果！\n"
                    f"国家 {attacker_nation_name} 攻击 {target_nation_name} 失败！\n"
                    f"{attacker_nation_name} 损失 {damage_dealt} 兵力，剩余 {attacker_nation_info['troops']}。\n"
                    f"{target_nation_name} 损失 {damage_taken} 兵力，剩余 {target_nation_info['troops']}。"
                )
                await self.send_text(result_msg)
                # 调用静态广播方法
                await WorldWarPlugin.broadcast_to_public_static(
                    result_msg,
                    self.get_config("game.announcement_chat_id", "12345678"),
                    self.get_config("game.enable_public_announcements", True)
                )

            try:
                WorldWarPlugin._save_game_data(game_data, data_file_path)
            except Exception as e:
                error_msg = f"保存游戏数据失败: {e}"
                await self.send_text(error_msg)
                return False, error_msg, True

            return True, None, True

    class ConquerCommand(BaseCommand):
        command_name = "conquer_command"
        command_description = "对指定国家发起掠夺领土"
        command_pattern = r"^/conquer\s+(.+?)\s+(\d+)$"

        async def execute(self) -> tuple[bool, str | None, bool]:
            try:
                user_id = self.message.message_info.user_info.user_id
                raw_message = self.message.raw_message
            except AttributeError as e:
                error_msg = "无法获取用户信息或消息内容。"
                await self.send_text(error_msg)
                return False, error_msg, True

            data_file_path = "./World/data/game_data.json"
            try:
                game_data = WorldWarPlugin._load_game_data(data_file_path)
            except Exception as e:
                error_msg = f"加载游戏数据失败: {e}"
                await self.send_text(error_msg)
                return False, error_msg, True

            match = re.match(self.command_pattern, raw_message)
            if not match:
                error_msg = "命令格式错误。请使用: /conquer <国家名> <出兵数量>"
                await self.send_text(error_msg)
                return False, error_msg, True

            target_nation_name = match.group(1).strip()
            try:
                attack_troops = int(match.group(2))
            except ValueError:
                error_msg = "出兵数量必须是一个整数。"
                await self.send_text(error_msg)
                return False, error_msg, True

            attacker_nation_name = get_player_nation(game_data, user_id)
            if not attacker_nation_name:
                msg = "你尚未加入任何国家，无法发起掠夺。请先使用 /join <国家名>。"
                await self.send_text(msg)
                return True, msg, True

            attacker_nation_info = get_nation_info(game_data, attacker_nation_name)
            target_nation_info = get_nation_info(game_data, target_nation_name)

            if not target_nation_info:
                msg = f"国家 {target_nation_name} 不存在。"
                await self.send_text(msg)
                return True, msg, True

            if attacker_nation_name == target_nation_name:
                msg = "你不能掠夺自己的国家。"
                await self.send_text(msg)
                return True, msg, True

            if are_allies(game_data, attacker_nation_name, target_nation_name):
                msg = f"你的国家 {attacker_nation_name} 与 {target_nation_name} 是盟友，无法发起掠夺。"
                await self.send_text(msg)
                return True, msg, True

            if attack_troops <= 0:
                msg = "出兵数量必须大于0。"
                await self.send_text(msg)
                return True, msg, True

            if attacker_nation_info["troops"] < attack_troops:
                msg = f"你的国家兵力不足。当前兵力: {attacker_nation_info['troops']}。"
                await self.send_text(msg)
                return True, msg, True

            effective_defense_troops = max(1, target_nation_info["troops"] - target_nation_info.get("deployed_troops", 0))
            attack_power = attack_troops * random.uniform(0.8, 1.2)
            defense_power = effective_defense_troops * random.uniform(0.8, 1.2)

            if attack_power > defense_power:
                damage_dealt = min(int(attack_troops * 0.2), target_nation_info["troops"])
                damage_taken = min(int(effective_defense_troops * 0.15), attack_troops)
                territory_gained = max(1, int(target_nation_info.get("territory", 15) * 0.05))

                attacker_nation_info["troops"] -= damage_taken
                target_nation_info["troops"] -= damage_dealt
                attacker_nation_info["territory"] = attacker_nation_info.get("territory", 15) + territory_gained
                target_nation_info["territory"] = max(1, target_nation_info.get("territory", 15) - territory_gained)

                # 直接通过 self.get_config 访问配置
                attacker_elo_change, target_elo_change = calculate_elo_change(
                    attacker_nation_info.get("elo", 1500),
                    target_nation_info.get("elo", 1500),
                    1.0,
                    self.get_config("game.elo_k_factor", 32.0)
                )
                attacker_nation_info["elo"] = max(100, attacker_nation_info.get("elo", 1500) + attacker_elo_change)
                target_nation_info["elo"] = max(100, target_nation_info.get("elo", 1500) + target_elo_change)

                result_msg = (
                    f"🏴 掠夺结果！\n"
                    f"国家 {attacker_nation_name} 成功掠夺了 {target_nation_name} 的 {territory_gained} 单位领土！\n"
                    f"{attacker_nation_name} 损失 {damage_taken} 兵力，剩余 {attacker_nation_info['troops']}。\n"
                    f"{target_nation_name} 损失 {damage_dealt} 兵力，剩余 {target_nation_info['troops']}。"
                )
                await self.send_text(result_msg)
                # 调用静态广播方法
                await WorldWarPlugin.broadcast_to_public_static(
                    result_msg,
                    self.get_config("game.announcement_chat_id", "12345678"),
                    self.get_config("game.enable_public_announcements", True)
                )

            else:
                damage_dealt = min(int(effective_defense_troops * 0.2), attack_troops)
                damage_taken = min(int(attack_troops * 0.15), effective_defense_troops)
                territory_lost = max(1, int(attacker_nation_info.get("territory", 15) * 0.03))

                attacker_nation_info["troops"] -= damage_dealt
                target_nation_info["troops"] -= damage_taken
                attacker_nation_info["territory"] = max(1, attacker_nation_info.get("territory", 15) - territory_lost)
                target_nation_info["territory"] = target_nation_info.get("territory", 15) + territory_lost

                # 直接通过 self.get_config 访问配置
                attacker_elo_change, target_elo_change = calculate_elo_change(
                    attacker_nation_info.get("elo", 1500),
                    target_nation_info.get("elo", 1500),
                    0.0,
                    self.get_config("game.elo_k_factor", 32.0)
                )
                attacker_nation_info["elo"] = max(100, attacker_nation_info.get("elo", 1500) + attacker_elo_change)
                target_nation_info["elo"] = max(100, target_nation_info.get("elo", 1500) + target_elo_change)

                result_msg = (
                    f"🏴 掠夺结果！\n"
                    f"国家 {attacker_nation_name} 掠夺 {target_nation_name} 失败！\n"
                    f"{attacker_nation_name} 损失 {damage_dealt} 兵力和 {territory_lost} 单位领土，剩余兵力 {attacker_nation_info['troops']}，剩余领土 {attacker_nation_info['territory']}。\n"
                    f"{target_nation_name} 损失 {damage_taken} 兵力。"
                )
                await self.send_text(result_msg)
                # 调用静态广播方法
                await WorldWarPlugin.broadcast_to_public_static(
                    result_msg,
                    self.get_config("game.announcement_chat_id", "12345678"),
                    self.get_config("game.enable_public_announcements", True)
                )

            try:
                WorldWarPlugin._save_game_data(game_data, data_file_path)
            except Exception as e:
                error_msg = f"保存游戏数据失败: {e}"
                await self.send_text(error_msg)
                return False, error_msg, True

            return True, None, True

    class AllyCommand(BaseCommand):
        command_name = "ally_command"
        command_description = "与指定国家结盟"
        command_pattern = r"^/ally\s+(.+)$"

        async def execute(self) -> tuple[bool, str | None, bool]:
            try:
                user_id = self.message.message_info.user_info.user_id
                raw_message = self.message.raw_message
            except AttributeError as e:
                error_msg = "无法获取用户信息或消息内容。"
                await self.send_text(error_msg)
                return False, error_msg, True

            data_file_path = "./World/data/game_data.json"
            try:
                game_data = WorldWarPlugin._load_game_data(data_file_path)
            except Exception as e:
                error_msg = f"加载游戏数据失败: {e}"
                await self.send_text(error_msg)
                return False, error_msg, True

            match = re.match(self.command_pattern, raw_message)
            if not match:
                error_msg = "命令格式错误。请使用: /ally <国家名>"
                await self.send_text(error_msg)
                return False, error_msg, True

            ally_nation_name = match.group(1).strip()
            player_nation_name = get_player_nation(game_data, user_id)

            if not player_nation_name:
                msg = "你尚未加入任何国家，无法结盟。请先使用 /join <国家名>。"
                await self.send_text(msg)
                return True, msg, True

            if player_nation_name == ally_nation_name:
                msg = "你不能与自己的国家结盟。"
                await self.send_text(msg)
                return True, msg, True

            player_nation_info = get_nation_info(game_data, player_nation_name)
            ally_nation_info = get_nation_info(game_data, ally_nation_name)

            if not ally_nation_info:
                msg = f"国家 {ally_nation_name} 不存在。"
                await self.send_text(msg)
                return True, msg, True

            if are_allies(game_data, player_nation_name, ally_nation_name):
                msg = f"你的国家 {player_nation_name} 已经与 {ally_nation_name} 是盟友了。"
                await self.send_text(msg)
                return True, msg, True

            if not player_nation_info.get("leader") or player_nation_info["leader"] != user_id:
                msg = "只有国家领袖才能发起结盟。"
                await self.send_text(msg)
                return True, msg, True

            if not ally_nation_info.get("leader"):
                msg = f"国家 {ally_nation_name} 尚未选出领袖，无法结盟。"
                await self.send_text(msg)
                return True, msg, True

            alliances = game_data.setdefault("alliances", set())
            if not isinstance(alliances, set):
                alliances = set(frozenset(a) for a in alliances) if isinstance(alliances, list) else set()
                game_data["alliances"] = alliances

            new_alliance = frozenset([player_nation_name, ally_nation_name])
            alliances.add(new_alliance)

            msg = f"🤝 国家 {player_nation_name} 与 {ally_nation_name} 成功结盟！"
            await self.send_text(msg)
            # 调用静态广播方法
            await WorldWarPlugin.broadcast_to_public_static(
                msg,
                self.get_config("game.announcement_chat_id", "12345678"),
                self.get_config("game.enable_public_announcements", True)
            )
            try:
                WorldWarPlugin._save_game_data(game_data, data_file_path)
            except Exception as e:
                error_msg = f"保存游戏数据失败: {e}"
                await self.send_text(error_msg)
                return False, error_msg, True

            return True, msg, True

    class WithdrawCommand(BaseCommand):
        command_name = "withdraw_command"
        command_description = "解除与指定国家的盟友关系"
        command_pattern = r"^/withdraw\s+(.+)$"

        async def execute(self) -> tuple[bool, str | None, bool]:
            try:
                user_id = self.message.message_info.user_info.user_id
                raw_message = self.message.raw_message
            except AttributeError as e:
                error_msg = "无法获取用户信息或消息内容。"
                await self.send_text(error_msg)
                return False, error_msg, True

            data_file_path = "./World/data/game_data.json"
            try:
                game_data = WorldWarPlugin._load_game_data(data_file_path)
            except Exception as e:
                error_msg = f"加载游戏数据失败: {e}"
                await self.send_text(error_msg)
                return False, error_msg, True

            match = re.match(self.command_pattern, raw_message)
            if not match:
                error_msg = "命令格式错误。请使用: /withdraw <国家名>"
                await self.send_text(error_msg)
                return False, error_msg, True

            ally_nation_name = match.group(1).strip()
            player_nation_name = get_player_nation(game_data, user_id)

            if not player_nation_name:
                msg = "你尚未加入任何国家，无法解除盟约。请先使用 /join <国家名>。"
                await self.send_text(msg)
                return True, msg, True

            if player_nation_name == ally_nation_name:
                msg = "你不能与自己的国家解除盟约。"
                await self.send_text(msg)
                return True, msg, True

            player_nation_info = get_nation_info(game_data, player_nation_name)
            ally_nation_info = get_nation_info(game_data, ally_nation_name)

            if not ally_nation_info:
                msg = f"国家 {ally_nation_name} 不存在。"
                await self.send_text(msg)
                return True, msg, True

            if not are_allies(game_data, player_nation_name, ally_nation_name):
                msg = f"你的国家 {player_nation_name} 与 {ally_nation_name} 并非盟友。"
                await self.send_text(msg)
                return True, msg, True

            if not player_nation_info.get("leader") or player_nation_info["leader"] != user_id:
                msg = "只有国家领袖才能解除盟约。"
                await self.send_text(msg)
                return True, msg, True

            alliances = game_data.get("alliances", set())
            if not isinstance(alliances, set):
                alliances = set(frozenset(a) for a in alliances) if isinstance(alliances, list) else set()
                game_data["alliances"] = alliances

            alliance_to_remove = None
            for alliance in alliances:
                if isinstance(alliance, (list, tuple, set, frozenset)):
                    if player_nation_name in alliance and ally_nation_name in alliance:
                        alliance_to_remove = alliance
                        break

            if alliance_to_remove:
                alliances.remove(alliance_to_remove)
                msg = f"💔 国家 {player_nation_name} 与 {ally_nation_name} 的盟约已解除。"
                await self.send_text(msg)
                # 调用静态广播方法
                await WorldWarPlugin.broadcast_to_public_static(
                    msg,
                    self.get_config("game.announcement_chat_id", "12345678"),
                    self.get_config("game.enable_public_announcements", True)
                )
                try:
                    WorldWarPlugin._save_game_data(game_data, data_file_path)
                except Exception as e:
                    error_msg = f"保存游戏数据失败: {e}"
                    await self.send_text(error_msg)
                    return False, error_msg, True
                return True, msg, True
            else:
                msg = f"未找到 {player_nation_name} 与 {ally_nation_name} 的盟约记录。"
                await self.send_text(msg)
                return True, msg, True

    class DeployCommand(BaseCommand):
        command_name = "deploy_command"
        command_description = "在自己的领土上驻军"
        command_pattern = r"^/deploy\s+(-?\d+)$"

        async def execute(self) -> tuple[bool, str | None, bool]:
            try:
                user_id = self.message.message_info.user_info.user_id
                raw_message = self.message.raw_message
            except AttributeError as e:
                error_msg = "无法获取用户信息或消息内容。"
                await self.send_text(error_msg)
                return False, error_msg, True

            data_file_path = "./World/data/game_data.json"
            try:
                game_data = WorldWarPlugin._load_game_data(data_file_path)
            except Exception as e:
                error_msg = f"加载游戏数据失败: {e}"
                await self.send_text(error_msg)
                return False, error_msg, True

            match = re.match(self.command_pattern, raw_message)
            if not match:
                error_msg = "命令格式错误。请使用: /deploy <数量> (正数部署，负数撤回)"
                await self.send_text(error_msg)
                return False, error_msg, True

            try:
                troops_to_deploy = int(match.group(1))
            except ValueError:
                error_msg = "部署数量必须是一个整数。"
                await self.send_text(error_msg)
                return False, error_msg, True

            player_nation_name = get_player_nation(game_data, user_id)
            if not player_nation_name:
                msg = "你尚未加入任何国家，无法部署兵力。请先使用 /join <国家名>。"
                await self.send_text(msg)
                return True, msg, True

            player_nation_info = get_nation_info(game_data, player_nation_name)

            current_deployed = player_nation_info.get("deployed_troops", 0)
            new_deployed = current_deployed + troops_to_deploy

            if new_deployed < 0:
                msg = f"撤回兵力过多。当前部署 {current_deployed}，无法撤回 {abs(troops_to_deploy)}。"
                await self.send_text(msg)
                return True, msg, True

            if new_deployed > player_nation_info["troops"]:
                msg = f"兵力不足。国家总兵力 {player_nation_info['troops']}，当前已部署 {current_deployed}，无法再部署 {troops_to_deploy}。"
                await self.send_text(msg)
                return True, msg, True

            player_nation_info["deployed_troops"] = new_deployed
            action_word = "部署" if troops_to_deploy >= 0 else "撤回"
            msg = f"✅ 成功{action_word} {abs(troops_to_deploy)} 兵力。国家 {player_nation_name} 当前部署兵力: {new_deployed}。"

            try:
                WorldWarPlugin._save_game_data(game_data, data_file_path)
            except Exception as e:
                error_msg = f"保存游戏数据失败: {e}"
                await self.send_text(error_msg)
                return False, error_msg, True

            await self.send_text(msg)
            return True, msg, True

    class TransferCommand(BaseCommand):
        command_name = "transfer_command"
        command_description = "(领袖) 将国家兵力转移给同国玩家"
        command_pattern = r"^/transfer\s+(.+?)\s+(\d+)$"

        async def execute(self) -> tuple[bool, str | None, bool]:
            try:
                user_id = self.message.message_info.user_info.user_id
                raw_message = self.message.raw_message
            except AttributeError as e:
                error_msg = "无法获取用户信息或消息内容。"
                await self.send_text(error_msg)
                return False, error_msg, True

            data_file_path = "./World/data/game_data.json"
            try:
                game_data = WorldWarPlugin._load_game_data(data_file_path)
            except Exception as e:
                error_msg = f"加载游戏数据失败: {e}"
                await self.send_text(error_msg)
                return False, error_msg, True

            match = re.match(self.command_pattern, raw_message)
            if not match:
                error_msg = "命令格式错误。请使用: /transfer <用户ID> <兵力数量>"
                await self.send_text(error_msg)
                return False, error_msg, True

            target_user_id = match.group(1).strip()
            try:
                transfer_amount = int(match.group(2))
            except ValueError:
                error_msg = "兵力数量必须是一个整数。"
                await self.send_text(error_msg)
                return False, error_msg, True

            player_nation_name = get_player_nation(game_data, user_id)
            if not player_nation_name:
                msg = "你尚未加入任何国家。"
                await self.send_text(msg)
                return True, msg, True

            player_nation_info = get_nation_info(game_data, player_nation_name)
            if not player_nation_info.get("leader") or player_nation_info["leader"] != user_id:
                msg = "只有国家领袖才能转移兵力。"
                await self.send_text(msg)
                return True, msg, True

            target_player_info = get_player_info(game_data, target_user_id)
            if not target_player_info or target_player_info.get("nation") != player_nation_name:
                msg = f"用户 {target_user_id} 不是你国家的成员。"
                await self.send_text(msg)
                return True, msg, True

            if transfer_amount <= 0:
                msg = "转移兵力数量必须大于0。"
                await self.send_text(msg)
                return True, msg, True

            if player_nation_info["troops"] < transfer_amount:
                msg = f"国家兵力不足。当前兵力: {player_nation_info['troops']}。"
                await self.send_text(msg)
                return True, msg, True

            player_nation_info["troops"] -= transfer_amount
            target_nation_info = get_nation_info(game_data, target_player_info["nation"])
            if target_nation_info:
                target_nation_info["troops"] += transfer_amount

            msg = f"✅ 成功将 {transfer_amount} 兵力从国家 {player_nation_name} 转移给玩家 {target_user_id}。"
            try:
                WorldWarPlugin._save_game_data(game_data, data_file_path)
            except Exception as e:
                error_msg = f"保存游戏数据失败: {e}"
                await self.send_text(error_msg)
                return False, error_msg, True

            await self.send_text(msg)
            return True, msg, True

    class AppointCommand(BaseCommand):
        command_name = "appoint_command"
        command_description = "(领袖) 任命官职"
        command_pattern = r"^/appoint\s+(.+?)\s+(.+)$"

        async def execute(self) -> tuple[bool, str | None, bool]:
            try:
                user_id = self.message.message_info.user_info.user_id
                raw_message = self.message.raw_message
            except AttributeError as e:
                error_msg = "无法获取用户信息或消息内容。"
                await self.send_text(error_msg)
                return False, error_msg, True

            data_file_path = "./World/data/game_data.json"
            try:
                game_data = WorldWarPlugin._load_game_data(data_file_path)
            except Exception as e:
                error_msg = f"加载游戏数据失败: {e}"
                await self.send_text(error_msg)
                return False, error_msg, True

            match = re.match(self.command_pattern, raw_message)
            if not match:
                error_msg = "命令格式错误。请使用: /appoint <用户ID> <军衔>"
                await self.send_text(error_msg)
                return False, error_msg, True

            target_user_id = match.group(1).strip()
            new_rank = match.group(2).strip()

            # --- 添加违禁词检查 (对军衔名称) ---
            if contains_banned_words(new_rank):
                msg = "❌ 军衔名称包含不适当的内容，请重新输入。"
                await self.send_text(msg)
                return True, msg, True # 成功处理请求，但拦截消息
            # --- 违禁词检查结束 ---

            if new_rank not in RANK_NAMES:
                available_ranks = ", ".join(RANK_NAMES)
                msg = f"无效的军衔 '{new_rank}'。可用军衔: {available_ranks}"
                await self.send_text(msg)
                return True, msg, True

            player_nation_name = get_player_nation(game_data, user_id)
            if not player_nation_name:
                msg = "你尚未加入任何国家。"
                await self.send_text(msg)
                return True, msg, True

            player_nation_info = get_nation_info(game_data, player_nation_name)
            if not player_nation_info.get("leader") or player_nation_info["leader"] != user_id:
                msg = "只有国家领袖才能任命官职。"
                await self.send_text(msg)
                return True, msg, True

            target_player_info = get_player_info(game_data, target_user_id)
            if not target_player_info or target_player_info.get("nation") != player_nation_name:
                msg = f"用户 {target_user_id} 不是你国家的成员。"
                await self.send_text(msg)
                return True, msg, True

            old_rank = target_player_info.get("rank", "士兵")
            target_player_info["rank"] = new_rank
            msg = f"🎖️ 成功将玩家 {target_user_id} 的军衔从 '{old_rank}' 任命为 '{new_rank}'。"

            try:
                WorldWarPlugin._save_game_data(game_data, data_file_path)
            except Exception as e:
                error_msg = f"保存游戏数据失败: {e}"
                await self.send_text(error_msg)
                return False, error_msg, True

            await self.send_text(msg)
            return True, msg, True

    class SetIdeologyCommand(BaseCommand):
        command_name = "set_ideology_command"
        command_description = "(领袖) 设置国家制度"
        command_pattern = r"^/set_ideology\s+(.+)$"

        async def execute(self) -> tuple[bool, str | None, bool]:
            try:
                user_id = self.message.message_info.user_info.user_id
                raw_message = self.message.raw_message
            except AttributeError as e:
                error_msg = "无法获取用户信息或消息内容。"
                await self.send_text(error_msg)
                return False, error_msg, True

            data_file_path = "./World/data/game_data.json"
            try:
                game_data = WorldWarPlugin._load_game_data(data_file_path)
            except Exception as e:
                error_msg = f"加载游戏数据失败: {e}"
                await self.send_text(error_msg)
                return False, error_msg, True

            match = re.match(self.command_pattern, raw_message)
            if not match:
                error_msg = "命令格式错误。请使用: /set_ideology <制度>"
                await self.send_text(error_msg)
                return False, error_msg, True

            new_ideology = match.group(1).strip()

            # --- 添加违禁词检查 (对制度名称) ---
            if contains_banned_words(new_ideology):
                msg = "❌ 国家制度名称包含不适当的内容，请重新输入。"
                await self.send_text(msg)
                return True, msg, True # 成功处理请求，但拦截消息
            # --- 违禁词检查结束 ---

            if new_ideology not in IDEOLOGIES:
                available_ideologies = ", ".join(IDEOLOGIES)
                msg = f"无效的制度 '{new_ideology}'。可用制度: {available_ideologies}"
                await self.send_text(msg)
                return True, msg, True

            player_nation_name = get_player_nation(game_data, user_id)
            if not player_nation_name:
                msg = "你尚未加入任何国家。"
                await self.send_text(msg)
                return True, msg, True

            player_nation_info = get_nation_info(game_data, player_nation_name)
            if not player_nation_info.get("leader") or player_nation_info["leader"] != user_id:
                msg = "只有国家领袖才能设置国家制度。"
                await self.send_text(msg)
                return True, msg, True

            old_ideology = player_nation_info.get("ideology", "无")
            player_nation_info["ideology"] = new_ideology
            msg = (
                f"🏛️ 国家 {player_nation_name} 的制度已从 '{old_ideology}' 更改为 '{new_ideology}'。\n"
                f"{IDEOLOGY_EFFECTS.get(new_ideology, '制度效果未知。')}"
            )

            try:
                WorldWarPlugin._save_game_data(game_data, data_file_path)
            except Exception as e:
                error_msg = f"保存游戏数据失败: {e}"
                await self.send_text(error_msg)
                return False, error_msg, True

            await self.send_text(msg)
            return True, msg, True

    class HelpCommand(BaseCommand):
        """显示世界大战插件的帮助菜单"""
        command_name = "help_command"
        command_description = "显示世界大战插件的帮助菜单"
        command_pattern = r"^/help$"

        async def execute(self) -> tuple[bool, str | None, bool]:
            """执行显示帮助菜单命令"""
            help_msg = format_help_menu()
            await self.send_text(help_msg)
            return True, help_msg, True

    class NationCommand(BaseCommand):
        """查看自己国家的信息"""
        command_name = "nation_command"
        command_description = "查看自己国家的信息"
        command_pattern = r"^/nation$"

        async def execute(self) -> tuple[bool, str | None, bool]:
            """执行查看国家信息命令"""
            try:
                user_id = self.message.message_info.user_info.user_id
            except AttributeError as e:
                error_msg = "无法获取用户信息。"
                await self.send_text(error_msg)
                return False, error_msg, True

            data_file_path = "./World/data/game_data.json"
            try:
                game_data = WorldWarPlugin._load_game_data(data_file_path)
            except Exception as e:
                error_msg = f"加载游戏数据失败: {e}"
                await self.send_text(error_msg)
                return False, error_msg, True

            player_nation_name = get_player_nation(game_data, user_id)
            if not player_nation_name:
                msg = "你尚未加入任何国家。请先使用 /join <国家名>。"
                await self.send_text(msg)
                return True, msg, True

            nation_info = get_nation_info(game_data, player_nation_name)
            if not nation_info:
                 # 理论上不应该发生
                 msg = f"错误：无法找到你的国家 {player_nation_name} 的信息。"
                 await self.send_text(msg)
                 return True, msg, True

            # 格式化国家信息
            leader_info = game_data.get("players", {}).get(nation_info.get("leader", ""), {})
            leader_name = leader_info.get("user_id", "未知") if leader_info else "未知"
            members_count = len(nation_info.get("members", []))
            allies_list = get_allies(game_data, player_nation_name)
            allies_str = ", ".join(allies_list) if allies_list else "无"

            nation_info_str = (
                f"🌍 国家信息: {player_nation_name}\n"
                f"🎖️ 领袖: {leader_name}\n"
                f"👥 成员数: {members_count}\n"
                f"⚔️ 总兵力: {nation_info.get('troops', 0)}\n"
                f"🗺️ 领土: {nation_info.get('territory', 0)}\n"
                f"👥 人口: {nation_info.get('population', 0)}\n"
                f"🏛️ 制度: {nation_info.get('ideology', '未定')}\n"
                f"🏕️ 驻军: {nation_info.get('deployed_troops', 0)}\n"
                f"📈 Elo评分: {nation_info.get('elo', 1500)}\n"
                f"🤝 盟友: {allies_str}\n"
            )

            await self.send_text(nation_info_str)
            return True, nation_info_str, True

    # --- 新增 WorldCommand ---
    class WorldCommand(BaseCommand):
        """查看世界现状"""
        command_name = "world_command"
        command_description = "查看当前世界中的所有国家及其概况"
        command_pattern = r"^/world$"

        async def execute(self) -> tuple[bool, str | None, bool]:
            """执行查看世界现状命令"""
            # 直接使用相对路径
            data_file_path = "./World/data/game_data.json"
            try:
                # 直接调用插件类的静态方法加载数据
                game_data = WorldWarPlugin._load_game_data(data_file_path)
            except Exception as e:
                error_msg = f"加载游戏数据失败: {e}"
                await self.send_text(error_msg)
                return False, error_msg, True

            # 格式化世界信息
            world_info_str = self._format_world_info(game_data)
            await self.send_text(world_info_str)
            return True, world_info_str, True

        def _format_world_info(self, game_data: Dict[str, Any]) -> str:
            """将游戏数据格式化为世界信息字符串"""
            nations = game_data.get("nations", {})
            if not nations:
                return "🌍 当前世界地图上还没有任何国家。快使用 /join <国家名> 创建或加入一个吧！"

            info_lines = ["🌍 世界现状概览 🌍"]
            # 按总兵力降序排序
            sorted_nations = sorted(nations.items(), key=lambda item: item[1].get('troops', 0), reverse=True)
            
            total_troops = sum(nation_data.get('troops', 0) for nation_data in nations.values())
            total_territory = sum(nation_data.get('territory', 0) for nation_data in nations.values())
            total_nations = len(nations)

            info_lines.append(f"📊 全球统计: 共 {total_nations} 个国家, 总兵力 {total_troops}, 总领土 {total_territory}")
            info_lines.append("-" * 30) # 分隔线

            for i, (nation_name, nation_data) in enumerate(sorted_nations, start=1):
                leader_id = nation_data.get('leader', '未知')
                troops = nation_data.get('troops', 0)
                territory = nation_data.get('territory', 0)
                ideology = nation_data.get('ideology', '未定')
                members_count = len(nation_data.get('members', []))
                deployed_troops = nation_data.get('deployed_troops', 0)
                
                # 格式化每行信息
                info_lines.append(
                    f"{i}. 🏛️ {nation_name} "
                    f"(领袖: {leader_id}, 成员: {members_count}, "
                    f"兵力: {troops}, 驻军: {deployed_troops}, 领土: {territory}, 制度: {ideology})"
                )

            return "\n".join(info_lines)
    # --- 新增结束 ---

    # --- Action 组件 ---

    class RandomEventAction(BaseAction):
        """随机事件 Action"""
        action_name = "random_event_action"
        action_description = "触发一个随机的世界事件，例如丰收、瘟疫、技术突破等。"
        activation_type = ActionActivationType.RANDOM # 让麦麦有机会随机触发
        mode_enable = ChatMode.ALL # 在所有聊天模式下都可用

        async def execute(self) -> tuple[bool, str | None, bool]:
            """执行随机事件"""
            # 直接使用硬编码路径和静态方法
            data_file_path = "./World/data/game_data.json"
            game_data = WorldWarPlugin._load_game_data(data_file_path)

            # 检查时间间隔 (通过 self.get_config 访问配置)
            current_time = time.time()
            last_event_time = game_data.get("last_event_time", 0)
            min_interval = self.get_config("game.event_interval_min", 60) * 60 # 转换为秒
            max_interval = self.get_config("game.event_interval_max", 120) * 60 # 转换为秒
            next_event_time = last_event_time + random.randint(min_interval, max_interval)

            if current_time < next_event_time:
                # 时间未到，不触发
                return False, None, False

            # 时间到了，尝试触发事件
            if not game_data["nations"]:
                return False, None, False # 没有国家，不执行

            # 选择一个随机国家
            target_nation_name = random.choice(list(game_data["nations"].keys()))
            target_nation_info = game_data["nations"][target_nation_name]

            # 选择一个随机事件
            event = random.choice(RANDOM_EVENTS)
            effect_path_str = event["effect"]
            multiplier = event["multiplier"]

            # 解析 effect 路径 (例如 "nation.troops")
            effect_path = effect_path_str.split('.')
            if len(effect_path) != 2 or effect_path[0] != 'nation':
                print(f"随机事件 '{event['name']}' 的 effect 路径无效: {effect_path_str}")
                return False, None, False

            stat_key = effect_path[1]
            if stat_key not in target_nation_info:
                 print(f"随机事件 '{event['name']}' 试图修改不存在的国家属性: {stat_key}")
                 return False, None, False

            old_value = target_nation_info[stat_key]
            # 应用效果 (使用 int 确保整数结果，特别是对于 troops)
            new_value = int(old_value * multiplier)
            # 确保一些关键值不会变为0或负数
            if stat_key in ['troops', 'territory', 'population']:
                new_value = max(1, new_value)
            target_nation_info[stat_key] = new_value

            # 更新最后事件时间
            game_data["last_event_time"] = current_time

            # 构造事件消息
            event_msg = f"🌍 全球事件: {event['name']} 发生在 {target_nation_name}！{event['description']}该国的{stat_key}从 {old_value} 变为 {new_value}。"

            # 保存数据
            try:
                WorldWarPlugin._save_game_data(game_data, data_file_path)
            except Exception as e:
                print(f"随机事件保存数据失败: {e}")
                # 即使保存失败，也尝试广播消息

            # 广播到公屏 (调用静态方法)
            await WorldWarPlugin.broadcast_to_public_static(
                event_msg,
                self.get_config("game.announcement_chat_id", "12345678"),
                self.get_config("game.enable_public_announcements", True)
            )

            return True, event_msg, True # 执行成功，发送了消息，拦截（如果需要的话）
