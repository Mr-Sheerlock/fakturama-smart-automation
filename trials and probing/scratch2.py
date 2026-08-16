from pywinauto import Desktop


windows = Desktop(backend="uia").windows()
print (len(windows))
for w in windows:
    print(w.window_text(), w.element_info.control_type)

# print(fakturama.window_text())
fakturama = Desktop(backend="uia").window(
    title_re="Fakturama -"
)

fakturama.print_control_identifiers(filename="fakturama_controls.txt")