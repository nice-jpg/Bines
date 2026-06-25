# from device.translator import check_actions
# from device.adapter import AndroidDevice

# # device = AndroidDevice()
# # check_actions(device)

# # device.act('swipe_up', (100, 1000))
# # device.act('tap', (0, 0))
# # ui = device.dump_ui()
# # print(ui)


# from tools.optimize_xml import optimize

# xml0 = ''

# with open('workspace/11.xml', 'r') as xml:
#     xml0 = xml.read()

# xml1 = optimize(xml0)

# print(xml1)

from nScreen.shadow_root import ShadowConfig, start_shadow_service, stop_shadow_service

config = ShadowConfig.from_env()

start_result = start_shadow_service()

print(start_result)


stop_result = stop_shadow_service()
print(stop_result)


