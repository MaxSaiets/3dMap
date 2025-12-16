"""
Сервіс для експорту 3D моделей у формати STL та 3MF
Підтримка мультиколірного друку через окремі об'єкти в 3MF
"""
import trimesh
import trimesh.transformations
from typing import List, Optional, Tuple
import os
import numpy as np

def export_preview_parts_stl(
    output_prefix: str,
    mesh_items: List[Tuple[str, trimesh.Trimesh]],
    model_size_mm: float = 100.0,
    add_flat_base: bool = True,
    base_thickness_mm: float = 2.0,
    rotate_to_ground: bool = False,
) -> dict[str, str]:
    """
    Експортує окремі STL частини для стабільного прев'ю у браузері (з кольорами на фронтенді).
    ВАЖЛИВО: усі частини отримують однакові трансформації (center/scale/minZ), щоб ідеально збігатися.

    Повертає мапу: {"base": "..._base.stl", "roads": "..._roads.stl", ...}
    """
    if not mesh_items:
        raise ValueError("Немає мешів для preview parts")

    # Робочі копії
    working_items: List[Tuple[str, trimesh.Trimesh]] = [(n, m.copy()) for n, m in mesh_items if m is not None]
    working_items = [(n, m) for n, m in working_items if len(m.vertices) > 0 and len(m.faces) > 0]
    if not working_items:
        raise ValueError("Усі preview меші порожні")

    # Об'єднуємо для розрахунку трансформацій
    combined = trimesh.util.concatenate([m for _, m in working_items])
    if combined is None or len(combined.vertices) == 0 or len(combined.faces) == 0:
        raise ValueError("Об'єднаний preview меш порожній")

    # Додаємо плоску базу ТІЛЬКИ коли рельєф вимкнено
    if add_flat_base:
        bounds_for_base = combined.bounds
        size_for_base = bounds_for_base[1] - bounds_for_base[0]
        center_for_base = (bounds_for_base[0] + bounds_for_base[1]) / 2.0
        margin = min(0.01 * max(size_for_base[0], size_for_base[1]), model_size_mm * 0.05)
        base_size = [
            max(size_for_base[0] + 2 * margin, model_size_mm * 0.5),
            max(size_for_base[1] + 2 * margin, model_size_mm * 0.5),
            max(base_thickness_mm, 0.8),
        ]
        min_z = bounds_for_base[0][2]
        base_center_z = min_z - base_size[2] / 2.0
        base_box = trimesh.creation.box(
            extents=base_size,
            transform=trimesh.transformations.translation_matrix(
                [center_for_base[0], center_for_base[1], base_center_z]
            ),
        )
        working_items.append(("BaseFlat", base_box))
        combined = trimesh.util.concatenate([combined, base_box])

    # Обчислюємо послідовність трансформацій (як у 3MF експорті)
    transforms: List[np.ndarray] = []
    combined_work = combined.copy()

    # 1) Центр по centroid
    center = combined_work.centroid
    t0 = trimesh.transformations.translation_matrix(-center)
    combined_work.apply_translation(-center)
    transforms.append(t0)

    # 2) Scale XY до model_size_mm
    bounds_after = combined_work.bounds
    size_after = bounds_after[1] - bounds_after[0]
    avg_xy_dimension = (size_after[0] + size_after[1]) / 2.0
    if avg_xy_dimension > 0:
        scale_factor = model_size_mm / avg_xy_dimension
        s = trimesh.transformations.scale_matrix(scale_factor)
        combined_work.apply_transform(s)
        transforms.append(s)

    # 3) ВАЖЛИВО: не робимо штучного Z-scale.
    # Це спотворює висоти доріг/будівель і дає ефект "дороги занадто високі".
    # Товщину бази/рельєфу забезпечуємо на етапі генерації (terrain_base_thickness_mm / BaseFlat).

    # 4) Опційний поворот (зазвичай вимкнено)
    if rotate_to_ground:
        rot_x = trimesh.transformations.rotation_matrix(-np.pi / 2, [1, 0, 0])
        combined_work.apply_transform(rot_x)
        transforms.append(rot_x)

    # 5) Center by bounds, minZ->0, center XY only (Z=0 лишається платформою)
    final_bounds = combined_work.bounds
    final_center_from_bounds = (final_bounds[0] + final_bounds[1]) / 2.0
    t_center = trimesh.transformations.translation_matrix(-final_center_from_bounds)
    combined_work.apply_translation(-final_center_from_bounds)
    transforms.append(t_center)

    final_bounds_after = combined_work.bounds
    min_z2 = final_bounds_after[0][2]
    t_minz = trimesh.transformations.translation_matrix([0, 0, -min_z2])
    combined_work.apply_translation([0, 0, -min_z2])
    transforms.append(t_minz)

    final_centroid_before = combined_work.centroid
    t_xy = trimesh.transformations.translation_matrix([-final_centroid_before[0], -final_centroid_before[1], 0.0])
    combined_work.apply_translation([-final_centroid_before[0], -final_centroid_before[1], 0.0])
    transforms.append(t_xy)

    # Експортуємо частини
    outputs: dict[str, str] = {}
    part_map = {
        "Base": "base",
        "BaseFlat": "base",
        "Roads": "roads",
        "Buildings": "buildings",
        "Water": "water",
        "Parks": "parks",
        "POI": "poi",
    }

    for name, mesh in working_items:
        part = part_map.get(name.split("_")[0])
        if not part:
            continue
        mesh_copy = mesh.copy()
        for mat in transforms:
            mesh_copy.apply_transform(mat)
        out_path = f"{output_prefix}_{part}.stl"
        mesh_copy.export(out_path, file_type="stl")
        outputs[part] = out_path

    return outputs


def export_scene(
    terrain_mesh: Optional[trimesh.Trimesh],
    road_mesh: Optional[trimesh.Trimesh],
    building_meshes: List[trimesh.Trimesh],
    water_mesh: Optional[trimesh.Trimesh],
    filename: str,
    format: str = "3mf",
    model_size_mm: float = 100.0,  # Розмір моделі в міліметрах (за замовчуванням 100мм = 10см)
    add_flat_base: bool = True,
    base_thickness_mm: float = 2.0,
    parks_mesh: Optional[trimesh.Trimesh] = None,
    poi_mesh: Optional[trimesh.Trimesh] = None,
) -> None:
    """
    Експортує 3D сцену у файл
    
    Args:
        terrain_mesh: Меш рельєфу/бази
        road_mesh: Меш доріг
        building_meshes: Список мешів будівель
        water_mesh: Меш води (для булевого віднімання)
        filename: Шлях до файлу для збереження
        format: Формат експорту ("stl" або "3mf")
    """
    # Готуємо список об'єктів з іменами для кольорів/окремого експорту
    mesh_items: List[Tuple[str, trimesh.Trimesh]] = []
    
    # 1. База/рельєф
    if terrain_mesh:
        # Перевіряємо валідність
        if len(terrain_mesh.vertices) > 0 and len(terrain_mesh.faces) > 0:
            # Виправляємо нормалі (якщо можливо)
            try:
                terrain_mesh.fix_normals()
            except Exception:
                pass  # Якщо не вдалося, продовжуємо
            mesh_items.append(("Base", terrain_mesh))
            print(f"Додано базу: {len(terrain_mesh.vertices)} вершин, {len(terrain_mesh.faces)} граней")
        else:
            print("Попередження: База порожня")
    
    # 2. Дороги
    if road_mesh:
        print(f"Додаємо дороги до сцени ({len(road_mesh.faces)} граней)")
        # Перевіряємо валідність
        if len(road_mesh.vertices) > 0 and len(road_mesh.faces) > 0:
            # Виправляємо нормалі (якщо можливо)
            try:
                road_mesh.fix_normals()
            except Exception:
                pass  # Якщо не вдалося, продовжуємо
            # Перекриття/посадка доріг робиться ще на етапі draping (road_embed),
            # тут більше не піднімаємо, бо це викликає "висять" після масштабування.
            mesh_items.append(("Roads", road_mesh))
            print(f"Дороги додані: {len(road_mesh.vertices)} вершин, {len(road_mesh.faces)} граней")
        else:
            print("Попередження: Дороги порожні")
    else:
        print("Попередження: Дороги не знайдено")
    
    # 3. Будівлі
    if building_meshes and len(building_meshes) > 0:
        print(f"Додаємо {len(building_meshes)} будівель до сцени")
        # Фільтруємо валідні будівлі
        valid_buildings = []
        for i, building in enumerate(building_meshes):
            if building is not None and len(building.vertices) > 0 and len(building.faces) > 0:
                try:
                    building.fix_normals()
                except Exception:
                    pass  # Якщо не вдалося, продовжуємо
                valid_buildings.append(building)
        
        if valid_buildings:
            if format.lower() == "3mf":
                # Для 3MF додаємо окремо, щоб слайсери могли розфарбовувати по об'єктах
                for i, building in enumerate(valid_buildings):
                    mesh_items.append((f"Building_{i}", building))
                print(f"Будівлі додані як окремі об'єкти: {len(valid_buildings)}")
            else:
                # Для STL можна об'єднати для меншого файлу
                try:
                    combined_buildings = trimesh.util.concatenate(valid_buildings)
                    mesh_items.append(("Buildings", combined_buildings))
                    print(f"Будівлі об'єднано ({len(combined_buildings.vertices)} вершин, {len(combined_buildings.faces)} граней)")
                except Exception as e:
                    print(f"Помилка об'єднання будівель: {e}")
                    for i, building in enumerate(valid_buildings[:100]):
                        mesh_items.append((f"Building_{i}", building))
        else:
            print("Попередження: Немає валідних будівель")
    else:
        print("Попередження: Будівлі не знайдено або не вдалося обробити")
    
    # 4. Вода (як окремий об'єкт для мультиколірного друку)
    if water_mesh:
        if len(water_mesh.vertices) > 0 and len(water_mesh.faces) > 0:
            try:
                water_mesh.fix_normals()
            except Exception:
                pass  # Якщо не вдалося, продовжуємо
            if format == "3mf":
                mesh_items.append(("Water", water_mesh))
            # Для STL не додаємо воду окремо (вона має бути віднята від бази)

    # 5. Парки/зелень
    if parks_mesh is not None and len(parks_mesh.vertices) > 0 and len(parks_mesh.faces) > 0:
        try:
            parks_mesh.fix_normals()
        except Exception:
            pass
        mesh_items.append(("Parks", parks_mesh))

    # 6. POI (лавочки/фонтани) - як окремий об'єкт
    if poi_mesh is not None and len(poi_mesh.vertices) > 0 and len(poi_mesh.faces) > 0:
        try:
            poi_mesh.fix_normals()
        except Exception:
            pass
        mesh_items.append(("POI", poi_mesh))
    
    # Перевіряємо, що є хоча б один меш
    if not mesh_items:
        raise ValueError("Немає геометрії для експорту! Перевірте, що дані завантажені правильно.")
    
    print(f"Всього мешів для експорту: {len(mesh_items)}")
    total_vertices = sum(len(m.vertices) for _, m in mesh_items)
    total_faces = sum(len(m.faces) for _, m in mesh_items)
    print(f"Загальна статистика: {total_vertices} вершин, {total_faces} граней")
    
    # Експорт
    if format.lower() == "3mf":
        export_3mf(filename, mesh_items, model_size_mm, add_flat_base=add_flat_base, base_thickness_mm=base_thickness_mm)
    elif format.lower() == "stl":
        export_stl(filename, mesh_items, model_size_mm, add_flat_base=add_flat_base, base_thickness_mm=base_thickness_mm)
    else:
        raise ValueError(f"Невідомий формат: {format}")


def export_3mf(
    filename: str,
    mesh_items: List[Tuple[str, trimesh.Trimesh]],
    model_size_mm: float = 100.0,
    add_flat_base: bool = True,
    base_thickness_mm: float = 2.0,  # тонша база, щоб не перекривати модель
    rotate_to_ground: bool = False,  # Не крутимо, щоб не ламати орієнтацію
) -> None:
    """
    Експортує сцену у формат 3MF з підтримкою окремих об'єктів
    """
    try:
        if not mesh_items:
            raise ValueError("Сцена порожня, немає що експортувати")
        
        # Робоча копія списку
        working_items: List[Tuple[str, trimesh.Trimesh]] = [(n, m.copy()) for n, m in mesh_items]
        
        # Об'єднуємо для розрахунків трансформацій
        print(f"Об'єднання {len(working_items)} мешів для 3MF...")
        combined = trimesh.util.concatenate([m for _, m in working_items])
        
        if combined is None or len(combined.vertices) == 0 or len(combined.faces) == 0:
            raise ValueError("Об'єднаний меш порожній")
        
        print(f"Об'єднано: {len(combined.vertices)} вершин, {len(combined.faces)} граней")
        
        # Виправляємо нормалі
        try:
            combined.fix_normals()
        except Exception:
            pass
        
        # Додаємо плоску базу, якщо немає рельєфу
        if add_flat_base:
            bounds_for_base = combined.bounds
            size_for_base = bounds_for_base[1] - bounds_for_base[0]
            center_for_base = (bounds_for_base[0] + bounds_for_base[1]) / 2.0
            margin = min(0.01 * max(size_for_base[0], size_for_base[1]), model_size_mm * 0.05)  # обмежуємо поля
            base_size = [
                max(size_for_base[0] + 2 * margin, model_size_mm * 0.5),
                max(size_for_base[1] + 2 * margin, model_size_mm * 0.5),
                max(base_thickness_mm, 0.8)
            ]
            min_z = bounds_for_base[0][2]
            base_center_z = min_z - base_size[2] / 2.0
            base_box = trimesh.creation.box(
                extents=base_size,
                transform=trimesh.transformations.translation_matrix(
                    [center_for_base[0], center_for_base[1], base_center_z]
                )
            )
            working_items.append(("BaseFlat", base_box))
            combined = trimesh.util.concatenate([combined, base_box])
            print("Додано плоску базу товщиною", base_size[2], "мм з полями", margin, "мм")

        # Накопичуємо ВСІ трансформації (center+scale+zscale+align) і застосовуємо до кожного меша.
        transforms: List[Tuple[str, np.ndarray]] = []

        # 1) Центрування за centroid
        center = combined.centroid
        t0 = trimesh.transformations.translation_matrix(-center)
        combined.apply_translation(-center)
        transforms.append(("translate", t0))

        # 2) Масштабування XY до model_size_mm
        bounds_after = combined.bounds
        size_after = bounds_after[1] - bounds_after[0]
        avg_xy_dimension = (size_after[0] + size_after[1]) / 2.0
        if avg_xy_dimension > 0:
            scale_factor = model_size_mm / avg_xy_dimension
            s = trimesh.transformations.scale_matrix(scale_factor)
            combined.apply_transform(s)
            transforms.append(("scale", s))

        # 3) ВАЖЛИВО: не робимо штучного Z-scale (спотворює road/building heights).

        # 4) Опційний поворот
        if rotate_to_ground:
            try:
                angle_x = -np.pi / 2
                rot_x = trimesh.transformations.rotation_matrix(angle_x, [1, 0, 0])
                combined.apply_transform(rot_x)
                transforms.append(("rotate", rot_x))
                print("Поворот навколо X застосовано (модель покладено на XY)")
            except Exception as e:
                print(f"Попередження: не вдалося покласти модель на XY (3MF): {e}")

        # 5) Центрування за bounds + підняття minZ до 0 + центрування XY (Z не рухаємо після minZ)
        try:
            final_bounds = combined.bounds
            final_center_from_bounds = (final_bounds[0] + final_bounds[1]) / 2.0
            t_center = trimesh.transformations.translation_matrix(-final_center_from_bounds)
            combined.apply_translation(-final_center_from_bounds)

            final_bounds_after = combined.bounds
            min_z = final_bounds_after[0][2]
            t_minz = trimesh.transformations.translation_matrix([0, 0, -min_z])
            combined.apply_translation([0, 0, -min_z])

            # Додаткове центрування ЛИШЕ по X/Y, щоб Z=0 залишився "платформою" для друку
            final_centroid_before = combined.centroid
            t_xy = trimesh.transformations.translation_matrix([-final_centroid_before[0], -final_centroid_before[1], 0.0])
            combined.apply_translation([-final_centroid_before[0], -final_centroid_before[1], 0.0])

            transforms.append(("translate", t_center))
            transforms.append(("translate", t_minz))
            transforms.append(("translate", t_xy))

            final_bounds_check = combined.bounds
            final_centroid = combined.centroid
            print(f"Фінальні bounds: min={final_bounds_check[0]}, max={final_bounds_check[1]}")
            print(f"Фінальний центр (centroid): {final_centroid}")
        except Exception as e:
            print(f"Попередження: Центрування/вирівнювання не виконано: {e}")
        
        # Трансформуємо кожен меш окремо і додаємо до сцени з кольорами
        scene = trimesh.Scene()
        color_map = {
            "Base": [200, 200, 200, 255],
            "BaseFlat": [200, 200, 200, 255],
            "Roads": [30, 30, 30, 255],
            "Buildings": [180, 180, 180, 255],
            "Water": [50, 120, 200, 255],
            "Parks": [90, 140, 80, 255],
            "POI": [220, 180, 60, 255],
        }

        for name, mesh in working_items:
            mesh_copy = mesh.copy()
            for t_name, mat in transforms:
                mesh_copy.apply_transform(mat)
            # Застосовуємо колір
            color = color_map.get(name.split("_")[0], [160, 160, 160, 255])
            try:
                mesh_copy.visual = trimesh.visual.ColorVisuals(mesh_copy, face_colors=color)
            except Exception:
                pass
            scene.add_geometry(mesh_copy, node_name=name)
        
        # Trimesh підтримує 3MF експорт
        scene.export(filename, file_type="3mf")
        print(f"Експортовано 3MF: {filename}")
        
        # Перевіряємо розмір файлу
        file_size = os.path.getsize(filename)
        print(f"Розмір файлу: {file_size} байт")
        if file_size < 100:
            raise ValueError(f"Файл занадто малий ({file_size} байт), можливо експорт не вдався")
    except Exception as e:
        print(f"Помилка експорту 3MF: {e}")
        # Fallback на STL
        stl_filename = filename.replace(".3mf", ".stl")
        print(f"Спроба експортувати як STL: {stl_filename}")
        export_stl(stl_filename, mesh_items, model_size_mm, add_flat_base=add_flat_base, base_thickness_mm=base_thickness_mm, rotate_to_ground=rotate_to_ground)


def export_stl(
    filename: str,
    mesh_items: List[Tuple[str, trimesh.Trimesh]],
    model_size_mm: float = 100.0,
    add_flat_base: bool = True,
    base_thickness_mm: float = 2.0,  # тонша база
    rotate_to_ground: bool = False,  # Не крутимо, щоб не ламати орієнтацію
) -> None:
    """
    Експортує сцену у формат STL
    """
    try:
        if not mesh_items:
            raise ValueError("Немає мешів для експорту")
        
        # Робоча копія
        working_items: List[Tuple[str, trimesh.Trimesh]] = [(n, m.copy()) for n, m in mesh_items]
        
        # Об'єднуємо всі геометрії в один меш
        print(f"Об'єднання {len(working_items)} мешів...")
        combined = trimesh.util.concatenate([m for _, m in working_items])
        
        # Перевіряємо результат
        if combined is None or len(combined.vertices) == 0 or len(combined.faces) == 0:
            raise ValueError("Об'єднаний меш порожній")
        
        print(f"Об'єднано: {len(combined.vertices)} вершин, {len(combined.faces)} граней")
        
        # Виправляємо нормалі перед експортом (якщо можливо)
        try:
            combined.fix_normals()
        except Exception:
            pass  # Якщо не вдалося, продовжуємо
        
        # Додаємо плоску базу, якщо немає рельєфу
        if add_flat_base:
            bounds_for_base = combined.bounds
            size_for_base = bounds_for_base[1] - bounds_for_base[0]
            center_for_base = (bounds_for_base[0] + bounds_for_base[1]) / 2.0
            margin = min(0.01 * max(size_for_base[0], size_for_base[1]), model_size_mm * 0.05)
            base_size = [
                max(size_for_base[0] + 2 * margin, model_size_mm * 0.5),
                max(size_for_base[1] + 2 * margin, model_size_mm * 0.5),
                max(base_thickness_mm, 0.8)
            ]
            min_z = bounds_for_base[0][2]
            base_center_z = min_z - base_size[2] / 2.0
            base_box = trimesh.creation.box(
                extents=base_size,
                transform=trimesh.transformations.translation_matrix(
                    [center_for_base[0], center_for_base[1], base_center_z]
                )
            )
            working_items.append(("BaseFlat", base_box))
            combined = trimesh.util.concatenate([combined, base_box])
            print("Додано плоску базу товщиною", base_size[2], "мм з полями", margin, "мм")

        # Перевіряємо розміри моделі
        bounds = combined.bounds
        size = bounds[1] - bounds[0]
        center = combined.centroid
        print(f"Розміри моделі: {size[0]:.2f} x {size[1]:.2f} x {size[2]:.2f}")
        print(f"Центр моделі: {center}")
        
        # Центруємо модель на початку координат (критично для відображення!)
        print("Центрування моделі...")
        combined.apply_translation(-center)
        
        # Перевіряємо розміри після центрування
        bounds_after = combined.bounds
        size_after = bounds_after[1] - bounds_after[0]
        print(f"Розміри після центрування: {size_after[0]:.2f} x {size_after[1]:.2f} x {size_after[2]:.2f}")
        
        # Масштабуємо модель до заданого розміру (в міліметрах)
        # Використовуємо середнє арифметичне X та Y для більш збалансованого масштабування
        # Це запобігає створенню дуже вузьких моделей
        avg_xy_dimension = (size_after[0] + size_after[1]) / 2
        
        # Перевіряємо, чи модель не занадто мала (менше 0.1 мм) - це може бути помилка
        if avg_xy_dimension < 0.001:
            print(f"⚠️ Попередження: Модель дуже мала ({avg_xy_dimension:.6f} мм), можливо проблема з даними")
            # Якщо модель занадто мала, не масштабуємо - залишаємо як є
        elif avg_xy_dimension > 0:
            # Конвертуємо міліметри в одиниці моделі (1 одиниця = 1 мм)
            # Масштабуємо так, щоб середній розмір X/Y був model_size_mm
            scale_factor = model_size_mm / avg_xy_dimension
            print(f"Масштабування моделі до {model_size_mm}мм (середній X/Y, коефіцієнт: {scale_factor:.6f})...")
            
            # Перевіряємо, чи коефіцієнт не занадто великий (може бути помилка)
            if scale_factor > 1000000:
                print(f"⚠️ Попередження: Коефіцієнт масштабування занадто великий ({scale_factor:.2f}), можливо помилка")
                # Обмежуємо масштабування
                scale_factor = min(scale_factor, 1000.0)
                print(f"Обмежено до {scale_factor:.2f}")
            
            combined.apply_scale(scale_factor)
            
            # Перевіряємо розміри після масштабування
            bounds_scaled = combined.bounds
            size_scaled = bounds_scaled[1] - bounds_scaled[0]
            print(f"Розміри після масштабування: {size_scaled[0]:.2f} x {size_scaled[1]:.2f} x {size_scaled[2]:.2f} мм")
            # ВАЖЛИВО: не робимо примусового Z-scale (це спотворює висоти доріг/будівель
            # і дає ефект "дороги занадто високі" / "висять над землею").
        
        # Накопичуємо трансформації і застосовуємо їх і до об'єднаного, і до окремих мешів
        transforms: List[np.ndarray] = []

        if rotate_to_ground:
            try:
                angle_x = -np.pi / 2
                rot_x = trimesh.transformations.rotation_matrix(angle_x, [1, 0, 0])
                combined.apply_transform(rot_x)
                transforms.append(rot_x)
                print("Поворот навколо X застосовано (модель покладено на XY)")
            except Exception as e:
                print(f"Попередження: не вдалося покласти модель на XY (STL): {e}")

        try:
            final_bounds = combined.bounds
            final_center_from_bounds = (final_bounds[0] + final_bounds[1]) / 2.0
            t_center = trimesh.transformations.translation_matrix(-final_center_from_bounds)
            combined.apply_translation(-final_center_from_bounds)
            transforms.append(t_center)

            final_bounds_after = combined.bounds
            min_z = final_bounds_after[0][2]
            t_minz = trimesh.transformations.translation_matrix([0, 0, -min_z])
            combined.apply_translation([0, 0, -min_z])
            transforms.append(t_minz)

            # Центруємо лише X/Y, щоб Z=0 залишився площиною друку
            final_centroid_before = combined.centroid
            t_xy = trimesh.transformations.translation_matrix([-final_centroid_before[0], -final_centroid_before[1], 0.0])
            combined.apply_translation([-final_centroid_before[0], -final_centroid_before[1], 0.0])
            transforms.append(t_xy)

            final_bounds_check = combined.bounds
            final_centroid = combined.centroid
            print(f"Фінальні bounds: min={final_bounds_check[0]}, max={final_bounds_check[1]}")
            print(f"Фінальний центр (centroid): {final_centroid}")
        except Exception as e:
            print(f"Попередження: Центрування/вирівнювання не виконано: {e}")
        
        # Експортуємо об'єднаний (для STL одна геометрія)
        combined.export(filename, file_type="stl")
        print(f"Експортовано STL: {filename}")
        
        # Перевіряємо розмір файлу
        file_size = os.path.getsize(filename)
        print(f"Розмір файлу: {file_size} байт")
        if file_size < 84:  # Мінімальний розмір STL (80 байт заголовок + 4 байти кількість трикутників)
            raise ValueError(f"Файл занадто малий ({file_size} байт), можливо експорт не вдався")
        
    except Exception as e:
        print(f"Помилка експорту STL: {e}")
        import traceback
        traceback.print_exc()
        raise
