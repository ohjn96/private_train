"""응답이 느린 만큼 호출 간격 대기가 줄어드는지 (간격은 '보낸 시각'부터 잰다)."""
import time
import unittest

from core.rate_limit import RateLimiter


class SlowResponseTest(unittest.TestCase):
    def test_slow_response_counts_toward_interval(self):
        limiter = RateLimiter(1.0)
        limiter.wait()          # 첫 호출: 바로 나감
        time.sleep(1.05)        # 응답이 간격보다 오래 걸림
        waited = limiter.wait()
        self.assertEqual(waited, 0.0)

    def test_fast_response_waits_the_rest(self):
        limiter = RateLimiter(1.0)
        limiter.wait()
        time.sleep(0.4)         # 응답 0.4초
        waited = limiter.wait()
        self.assertAlmostEqual(waited, 0.6, delta=0.1)


if __name__ == '__main__':
    unittest.main()
