"""이름 유사도 매칭 테스트.

이 로직이 느슨해지면 '서로 다른 패키지를 같다고 자동병합' 하는 사고가 난다.
그래서 '붙어야 할 것' 뿐 아니라 '절대 붙으면 안 되는 것' 을 함께 고정한다.
"""
import pytest

from core.compare import AUTO_MERGE_SCORE
from core.matching import SCORE_LIKELY, SCORE_REVIEW, pair_score


# --------------------------------------------------------------------------
# 붙어야 하는 쌍 (실제로 관측된 표기 차이 - CASE-003)
# --------------------------------------------------------------------------

@pytest.mark.parametrize("a, b", [
    # BlackDuck KB 가 GitHub 저장소명(owner/repo)으로 기록하는 케이스
    ("feross/buffer", "buffer"),
    ("debug-js/debug", "debug"),
    ("wooorm/ccount", "ccount"),
    ("qix/color", "color"),
    ("micromatch/braces", "braces"),
    ("samccone/chrome-trace-event", "chrome-trace-event"),
    ("sindresorhus/globals", "globals"),
    ("webpack/loader-utils", "loader-utils"),
    # node- 접두
    ("node-lru-cache", "lru-cache"),
    ("node-concat-map", "concat-map"),
    ("node-entities", "entities"),
    ("node-ignore", "ignore"),
    # js 접미/접두
    ("markedjs", "marked"),
    ("d3-js", "d3"),
    ("immutable-js", "immutable"),
])
def test_같은_패키지_표기차이는_높은_점수(a, b):
    score = pair_score(a, b)
    assert score >= SCORE_LIKELY, f"{a} ↔ {b} = {score}"


def test_완전히_같으면_100점():
    assert pair_score("lodash", "lodash") == 100.0
    assert pair_score("Lodash", "lodash") == 100.0  # 대소문자 무시


# --------------------------------------------------------------------------
# 절대 붙으면 안 되는 쌍 (오병합 방지)
# --------------------------------------------------------------------------

@pytest.mark.parametrize("a, b", [
    # 이름은 비슷하지만 명백히 다른 패키지
    ("rehype-minify", "rehype-stringify"),
    ("postcss-normalize-string", "postcss-normalize-url"),
    ("@babel/parser", "@babel/traverse"),
    ("eslint-scope", "eslint-visitor-keys"),
    # 플랫폼별 바이너리는 서로 다른 패키지다
    ("@parcel/watcher-linux-x64-glibc", "@parcel/watcher-win32-x64"),
    ("@parcel/watcher-darwin-arm64", "@parcel/watcher-darwin-x64"),
    # 완전히 무관
    ("lodash", "webpack"),
])
def test_다른_패키지는_자동병합_기준_미만(a, b):
    """AUTO_MERGE_SCORE 이상이면 (버전까지 같을 때) 자동으로 합쳐지므로 위험하다."""
    score = pair_score(a, b)
    assert score < AUTO_MERGE_SCORE, f"{a} ↔ {b} = {score} (자동병합될 위험)"


def test_점수_기준선_순서():
    """기준값이 뒤집히면 분류 로직 전체가 무너진다."""
    assert SCORE_REVIEW < SCORE_LIKELY <= AUTO_MERGE_SCORE


def test_대칭성():
    """a↔b 와 b↔a 의 점수가 다르면 비교 방향에 따라 결과가 달라진다."""
    for a, b in [("feross/buffer", "buffer"), ("node-lru-cache", "lru-cache"),
                 ("rehype-minify", "rehype-stringify")]:
        assert pair_score(a, b) == pair_score(b, a), f"{a} ↔ {b}"
