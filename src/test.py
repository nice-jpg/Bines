from device.translator import check_actions
from device.adapter import AndroidDevice

device = AndroidDevice()
check_actions(device)

device.act('swipe_up', (0, 0))
device.act('tap', (0, 0))
ui = device.dump_ui()
print(ui)
