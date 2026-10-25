"""模拟 pytest:持有设备租约 20 秒(独立进程,跨进程文件锁)。"""
import sys, time
sys.path.insert(0, "src")
from mobile_auto import DeviceSession, Target, Locator, load_config

config = load_config(device_serial="749622c9", app_package="com.sinosoft.trainapp", app_activity=".MainActivity")
session = DeviceSession(config)
session.start()  # LEASED:测试进程持有租约
print("lease held", flush=True)
time.sleep(20)
session.close()
print("lease released", flush=True)
