"""Перевірка формату 3MF файлу"""
import zipfile
from pathlib import Path
import os

f = sorted(Path('output').glob('*.3mf'), key=os.path.getmtime, reverse=True)[0]
print(f'Файл: {f.name}')

z = zipfile.ZipFile(f)
print(f'\nФайли в 3MF:')
for name in z.namelist():
    print(f'  - {name}')

model_file = z.read('3D/3dmodel.model')
print(f'\nРозмір .model файлу: {len(model_file)} байт')
print(f'Перші 200 байт:')
print(model_file[:200])

is_xml = model_file[:5] == b'<?xml' or model_file[:5] == b'<model'
print(f'\nТип: {"XML (3MF формат)" if is_xml else "STL (бінарний)"}')

if not is_xml:
    print('Це STL файл - можна завантажити напряму!')
else:
    print('Це XML формат - потрібен парсер 3MF')

