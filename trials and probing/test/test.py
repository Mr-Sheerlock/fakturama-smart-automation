from pywinauto import Desktop


windows = Desktop(backend="uia").windows()
print (len(windows))
for w in windows:
    print(w.window_text(), w.element_info.control_type)

# print(fakturama.window_text())
fakturama = Desktop(backend="uia").window(
    title_re="Fakturama -"
)


# capture this to a file 

# with open("fakturama_controls.txt", "w", encoding="utf-8") as f:
#     for control in fakturama.descendants():
#         f.write(f"{control.window_text()} - {control.element_info.control_type}\n")


# capture this to a file using print_control_identifiers
# with open("fakturama_controls.txt", "w", encoding="utf-8") as f:
#     with f:
#         fakturama.print_control_identifiers(f)
fakturama.print_control_identifiers(filename="fakturama_controls debtor On.txt")