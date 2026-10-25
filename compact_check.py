"""缓存「一个正身 + 别名」的回归测试。

盯的是实机上最常见的那个 bug：同一句台词被 OCR 读成好几种样子，库里就存了好几条，
每条的译文还不一样，于是屏幕上同一句话在两套译文之间来回闪。

跑法：python compact_check.py  （全通过时 exit=0）
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from galtl import textutil
from galtl.store import Store

# 同一句台词的四种真实读法（取自《千恋＊万花》的实际缓存）
VARIANTS = [
    "電車を降リて、駅前でタクシーを捕まえたものの、いつの間に\nかうたたねしていたらしい。",
    "~・ユを降りて、駅前でタクシーを捕まえたものの、いつの間に\nかうたたねしていたら",
    "~・ユ降りて、駅前でタクシーそ\nかうたたねしていたらしい。",
    "電車を降ワて、駅前でタクシーを捕まえたものの、いつの間に\nかうたたねしていたらしい。",
]
OTHER = "お客さんってば、こんな時間まで起きているなんて"
ZH = "下了电车，在站前拦了辆出租车，但不知什么时候好像睡着了。"

fails: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(("  ok    " if ok else "  FAIL  ") + name + ("" if ok else "  " + detail))
    if not ok:
        fails.append(name)


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="compact_check_"))
    try:
        db = tmp / "cache.db"
        st = Store(db)

        # 1. 四种读法依次写入：写的时候就该收敛成一条（不靠事后合并）
        for i, jp in enumerate(VARIANTS):
            st.put(jp, {"zh": "%s#%d" % (ZH, i), "grammar": [{"pattern": "〜て"}] if i == 0 else []})
        check("put 时就收敛成一条", st.stats()["lines"] == 1, repr(st.stats()))

        # 2. 模拟「历史老库」：绕过 put 直接插三条不同读法（1.1.6 及更早就是这样攒起来的）
        import json as _json
        import time as _time

        for i, jp in enumerate(VARIANTS[1:], start=1):
            key = textutil.dedupe_key(jp)
            st._db().execute(
                "INSERT INTO lines(key,jp,payload,ts,hits) VALUES(?,?,?,?,1) "
                "ON CONFLICT(key) DO UPDATE SET payload=excluded.payload",
                (key, jp, _json.dumps({"zh": "%s#old%d" % (ZH, i)}, ensure_ascii=False),
                 int(_time.time()) + i),
            )
        st._db().commit()
        check("历史里积累出多条", st.stats()["lines"] >= 3, repr(st.stats()))

        # 3. 启动合并应该把它们并到一条
        merged = st.compact()["merged"]
        check("compact 合并了重复读法", merged >= 2, "merged=%d" % merged)

        rows = st.recent(200)
        keys = [textutil.dedupe_key(r.get("jp") or "") for r in rows]
        dup = 0
        for i in range(len(keys)):
            for j in range(i + 1, len(keys)):
                if keys[i] and keys[j] and textutil.similar_key(keys[i], keys[j]):
                    dup += 1
        check("合并后不再有近义重复对", dup == 0, "dup=%d" % dup)

        # 3. 合并后四种读法必须拿到同一份译文
        zs = []
        for jp in VARIANTS:
            got = st.get(jp) or st.find_similar(jp) or {}
            zs.append((got.get("zh") or "").split("#")[0])
        uniq = {z for z in zs if z}
        check("四种读法译文一致", len(uniq) == 1, repr(zs))

        # 4. put 新读法时不再生成第二份译文
        before = st.stats()["lines"]
        st.put("電車を降りて、駅前でタクシーを捕まえたものの、いつの間にかうたたねしていたらしい。",
               {"zh": "另一套译文"})
        check("put 新读法不新增行", st.stats()["lines"] == before, repr(st.stats()))
        got = st.get(VARIANTS[0]) or {}
        check("正身译文没被改写", (got.get("zh") or "").startswith(ZH), repr(got.get("zh"))[:40])

        # 5. 不相干的台词不能被并进来
        st.put(OTHER, {"zh": "我说客人啊，这么晚还不睡"})
        d = st.get(OTHER) or {}
        check("无关台词独立成行", (d.get("zh") or "").startswith("我说客人啊"), repr(d.get("zh")))

        # 6. 别名链要能一路跟到底（A→B→C）
        k1 = textutil.dedupe_key(VARIANTS[0])
        k2 = textutil.dedupe_key(VARIANTS[2])
        st._alias(k1, k2)
        st._alias(k2, k1)  # 故意造一个环，_canon 不能死循环
        got = st.get(VARIANTS[0]) or {}
        check("别名成环也不死循环", isinstance(got, dict), repr(got)[:60])

        st.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    if fails:
        print("失败项：", "、".join(fails))
        return 1
    print("结果：全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
