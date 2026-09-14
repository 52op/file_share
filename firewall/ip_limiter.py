# -*- coding: utf-8 -*-
from collections import defaultdict
import json
import os
import threading
import time


class IPLimiter:
    def __init__(self, max_attempts=5, block_time=300, persist_file=None):
        # 5次尝试，自动封禁300秒（5分钟）
        self.max_attempts = max_attempts
        self.block_time = block_time
        self.failed_attempts = defaultdict(int)  # 记录失败次数
        self.block_until = defaultdict(float)    # 记录自动封禁解除时间
        self.manual_blocks = {}                  # 手动封禁：ip -> 过期时间戳（0=永久）
        self.persist_file = persist_file
        self._lock = threading.Lock()
        self._load_persist()

    def _load_persist(self):
        """加载持久化的手动封禁列表"""
        if not self.persist_file or not os.path.exists(self.persist_file):
            return
        try:
            with open(self.persist_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            now = time.time()
            for ip, until in (data or {}).items():
                if until == 0 or until > now:
                    self.manual_blocks[ip] = until
        except Exception:
            pass

    def _save_persist(self):
        if not self.persist_file:
            return
        try:
            now = time.time()
            data = {ip: until for ip, until in self.manual_blocks.items()
                    if until == 0 or until > now}
            tmp = self.persist_file + '.tmp'
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self.persist_file)
        except Exception:
            pass

    def add_failed_attempt(self, ip):
        with self._lock:
            self.failed_attempts[ip] += 1
            if self.failed_attempts[ip] >= self.max_attempts:
                self.block_until[ip] = time.time() + self.block_time
                self.failed_attempts[ip] = 0  # 重置计数

    def is_blocked(self, ip):
        now = time.time()
        with self._lock:
            # 手动封禁优先
            if ip in self.manual_blocks:
                until = self.manual_blocks[ip]
                if until == 0 or until > now:
                    return True
                del self.manual_blocks[ip]
                self._save_persist()
            # 自动封禁
            if ip in self.block_until:
                if now < self.block_until[ip]:
                    return True
                del self.block_until[ip]
                del self.failed_attempts[ip]
        return False

    def get_remaining_time(self, ip):
        now = time.time()
        with self._lock:
            if ip in self.manual_blocks:
                until = self.manual_blocks[ip]
                if until == 0:
                    return 0  # 永久
                return int(until - now) if until > now else 0
            if ip in self.block_until:
                remaining = int(self.block_until[ip] - now)
                return remaining if remaining > 0 else 0
        return 0

    def reset(self, ip):
        with self._lock:
            if ip in self.failed_attempts:
                del self.failed_attempts[ip]
            if ip in self.block_until:
                del self.block_until[ip]

    # ---------- 手动封禁（审计页一键封禁） ----------

    def block(self, ip, minutes=None):
        """手动封禁。minutes=None 表示永久封禁（0）。"""
        with self._lock:
            if minutes is None:
                self.manual_blocks[ip] = 0
            else:
                self.manual_blocks[ip] = time.time() + int(minutes) * 60
            self.failed_attempts.pop(ip, None)
            self.block_until.pop(ip, None)
            self._save_persist()
        return self.manual_blocks[ip]

    def unblock(self, ip):
        """解除手动封禁（并清理自动封禁）。"""
        with self._lock:
            removed = ip in self.manual_blocks
            self.manual_blocks.pop(ip, None)
            self.failed_attempts.pop(ip, None)
            self.block_until.pop(ip, None)
            if removed:
                self._save_persist()
        return removed

    def list_blocked(self):
        """列出当前封禁中的 IP（手动 + 自动）。返回 [{ip, until, manual, permanent}]"""
        now = time.time()
        out = []
        with self._lock:
            for ip, until in self.manual_blocks.items():
                if until == 0 or until > now:
                    out.append({'ip': ip, 'until': until, 'manual': True, 'permanent': until == 0})
            for ip, until in self.block_until.items():
                if until > now:
                    out.append({'ip': ip, 'until': until, 'manual': False, 'permanent': False})
        return out
