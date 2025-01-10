from collections import defaultdict
import time

class IPLimiter:
    def __init__(self, max_attempts=5, block_time=300):  # 5次尝试，封禁300秒（5分钟）
        self.max_attempts = max_attempts
        self.block_time = block_time
        self.failed_attempts = defaultdict(int)  # 记录失败次数
        self.block_until = defaultdict(float)    # 记录封禁解除时间

    def add_failed_attempt(self, ip):
        self.failed_attempts[ip] += 1
        if self.failed_attempts[ip] >= self.max_attempts:
            self.block_until[ip] = time.time() + self.block_time
            self.failed_attempts[ip] = 0  # 重置计数

    def is_blocked(self, ip):
        if ip in self.block_until:
            if time.time() < self.block_until[ip]:
                return True
            else:
                # 封禁时间已过，清除记录
                del self.block_until[ip]
                del self.failed_attempts[ip]
        return False

    def get_remaining_time(self, ip):
        if ip in self.block_until:
            remaining = int(self.block_until[ip] - time.time())
            return remaining if remaining > 0 else 0
        return 0

    def reset(self, ip):
        if ip in self.failed_attempts:
            del self.failed_attempts[ip]
        if ip in self.block_until:
            del self.block_until[ip]
