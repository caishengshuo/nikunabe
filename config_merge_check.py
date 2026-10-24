"""配置合并回归：外部手改 config.json 后，程序内 save() 不应把外部改动冲掉。"""
import json
import time
from pathlib import Path

from galtl.config import Config

tmp = Path("G:\\日语\\_cfg_merge_test.json")
if tmp.exists():
    tmp.unlink()

# 初始：一个空 key 的配置
c1 = Config(tmp)
c1.set("api_key", "")
c1.set("poll_ms", 700)
c1.save()

# 模拟另一个进程（用户手改 / 第二个实例）写入 key
time.sleep(0.05)
disk = json.loads(tmp.read_text(encoding="utf-8"))
disk["api_key"] = "TESTKEY-external"
tmp.write_text(json.dumps(disk, ensure_ascii=False, indent=2), encoding="utf-8")
time.sleep(0.05)

# 程序内改了别的键后 save()
c1.set("poll_ms", 500)
c1.save()

after = json.loads(tmp.read_text(encoding="utf-8"))
print("外部写入的 api_key 是否保住：", after.get("api_key") == "TESTKEY-external")
print("程序内改的 poll_ms 是否生效：", after.get("poll_ms") == 500)

# 再验证：程序内主动改 key 时，应以内存为准
c1.set("api_key", "TESTKEY-app")
c1.save()
final = json.loads(tmp.read_text(encoding="utf-8"))
print("程序主动改 key 时优先生效：", final.get("api_key") == "TESTKEY-app")

tmp.unlink()

# 记事本「UTF-8 带 BOM」另存：以前 json.loads 会抛 Unexpected UTF-8 BOM，
# 整份配置静默退回默认值（用户以为设置丢了）。现在用 utf-8-sig 读取。
bom = Path("G:\\日语\\_cfg_bom_test.json")
bom.write_bytes(b"\xef\xbb\xbf" + json.dumps({"api_key": "TESTKEY-bom", "poll_ms": 321},
                                            ensure_ascii=False).encode("utf-8"))
c2 = Config(bom)
c2.load()
bom_ok = (c2.get("api_key") == "TESTKEY-bom" and c2.get("poll_ms") == 321)
print("带 BOM 的 config.json 能否正常读到：", bom_ok)
bom.unlink()

ok = (
    after.get("api_key") == "TESTKEY-external"
    and after.get("poll_ms") == 500
    and final.get("api_key") == "TESTKEY-app"
    and bom_ok
)
print("结论：", "合并逻辑正确" if ok else "合并逻辑有问题")
