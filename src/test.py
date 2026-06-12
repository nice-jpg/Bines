from device.translator import check_actions
from device.adapter import AndroidDevice

device = AndroidDevice()
check_actions(device)

device.act('touch', (100, 100))
ui = device.dump_ui()
print(ui)
device.