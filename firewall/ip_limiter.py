from collections import defaultdict
import time

class IPLimiter:
    def __init__(self, max_attempts=5, block_time=300):
        self.max_attempts = max_attempts
        self.block_time = block_time
        self.failed_attempts = defaultdict(int)
        self.block_until = defaultdict(float)

    def add_failed_attempt(self, ip):
        self.failed_attempts[ip] += 1
        if self.failed_attempts[ip] >= self.max_attempts:
            self.block_until[ip] = time.time() + self.block_time
            self.failed_attempts[ip] = 0

    def is_blocked(self, ip):
        if ip in self.block_until:
            if time.time() < self.block_until[ip]:
                return True
            else:

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
