import os

file_path = r'h:\3dMap\backend\services\road_processor.py'

new_process_roads_code = r'''def process_roads(
    G_roads,
    width_multiplier: float = 1.0,
    terrain_provider: Optional["TerrainProvider"] = None,
    road_height: float = 1.0,
    road_embed: float = 0.0,
    merged_roads: Optional[object] = None,
    water_geometries: Optional[List] = None,
    bridge_height_multiplier: float = 1.0,
    global_center: Optional["GlobalCenter"] = None,
    min_width_m: Optional[float] = None,
    clip_polygon: Optional[object] = None,
    city_cache_key: Optional[str] = None,
) -> Optional[trimesh.Trimesh]:
    """
    Обробляє дорожню мережу, створюючи 3D меші з правильною шириною.
    ВИПРАВЛЕНО: Конвертація в метри ДО обробки + Фікс для мостів над водою.
    """
    if G_roads is None:
        return None

    # 1. Отримуємо GeoDataFrame ребер
    gdf_edges = None
    if isinstance(G_roads, gpd.GeoDataFrame):
        gdf_edges = G_roads.copy()
    else:
        if not hasattr(G_roads, "edges") or len(G_roads.edges) == 0:
            return None
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            gdf_edges = ox.graph_to_gdfs(G_roads, nodes=False).copy()

    # 2. КРИТИЧНО: Конвертуємо дороги в локальні координати (метри) ПЕРЕД будь-якими діями
    if global_center is not None and not gdf_edges.empty:
        print("[INFO] Конвертація доріг в локальні координати (метри)...")
        def to_local_transform(x, y, z=None):
            x_local, y_local = global_center.to_local(x, y)
            if z is not None:
                return (x_local, y_local, z)
            return (x_local, y_local)
        
        try:
            # Перевіряємо, чи координати вже не в метрах (якщо > 100000)
            sample_geom = gdf_edges.iloc[0].geometry
            bounds = sample_geom.bounds
            if abs(bounds[0]) < 1000.0: # Якщо схоже на градуси (Lat/Lon)
                gdf_edges["geometry"] = gdf_edges["geometry"].apply(
                    lambda g: transform(to_local_transform, g) if g is not None and not g.is_empty else g
                )
        except Exception as e:
            print(f"[WARN] Помилка конвертації координат: {e}")

    # 3. Будуємо полігони доріг (вже в метрах, тому ширина буде правильною)
    if merged_roads is None:
        print("Створення буферів доріг...")
        merged_roads = build_road_polygons(gdf_edges, width_multiplier=width_multiplier, min_width_m=min_width_m)

    if merged_roads is None:
        return None
    
    # 4. Обрізка по зоні (Pre-clip)
    if clip_polygon is not None:
        try:
            # Переконуємось, що clip_polygon теж в локальних
            clip_poly_local = clip_polygon
            if global_center:
                 # Якщо clip_polygon у Lat/Lon, тут треба було б конвертувати, 
                 # але зазвичай він передається вже локальним з генератора тайлів.
                 pass 
            
            merged_roads = merged_roads.intersection(clip_poly_local)
            if merged_roads is None or merged_roads.is_empty:
                return None
        except Exception as e:
            print(f"[WARN] Помилка обрізки по зоні: {e}")

    # 5. Обрізка по рельєфу (Box) - щоб не виходити за межі даних висот
    if terrain_provider is not None:
        try:
            min_x, max_x, min_y, max_y = terrain_provider.get_bounds()
            clip_box = box(min_x - 200.0, min_y - 200.0, max_x + 200.0, max_y + 200.0)
            merged_roads = merged_roads.intersection(clip_box)
            if not merged_roads.is_valid:
                merged_roads = merged_roads.buffer(0)
        except Exception as e:
            pass

    # Розбиваємо на список полігонів
    road_geoms = []
    if isinstance(merged_roads, Polygon):
        road_geoms = [merged_roads]
    elif isinstance(merged_roads, MultiPolygon):
        road_geoms = list(merged_roads.geoms)
    else:
        try:
            road_geoms = [g for g in merged_roads.geoms if isinstance(g, Polygon)]
        except:
            pass

    # 6. Підготовка води (для детекти мостів)
    water_geoms_local = []
    if water_geometries:
        # Припускаємо, що water_geometries вже передані в правильній системі,
        # або конвертуємо їх тут, якщо треба. Зазвичай в pipeline вони вже локальні.
        water_geoms_local = water_geometries

    # 7. Визначення мостів (використовуємо локальний gdf_edges!)
    bridges = detect_bridges(gdf_edges, water_geometries=water_geoms_local, clip_polygon=clip_polygon)

    # Категоризація мостів
    bridges_low = [b for b in bridges if len(b) >= 5 and b[4] <= 1]
    bridges_high = [b for b in bridges if len(b) >= 5 and b[4] > 1]
    
    # Маска для вирізання (тільки низькі мости + мости над водою)
    cut_mask_polys = [b[1] for b in bridges_low if b[1] is not None]
    cut_mask_polys.extend([b[1] for b in bridges if len(b) >= 4 and b[3] and b[1] is not None])
    
    bridge_cut_union = None
    if cut_mask_polys:
        try:
            bridge_cut_union = unary_union(cut_mask_polys)
        except:
            pass

    print(f"Генерація 3D доріг ({len(road_geoms)} полігонів, {len(bridges)} мостів)...")
    road_meshes = []
    stats = {'bridge': 0, 'ground': 0, 'anti_drown': 0}

    # Попередня підготовка точок (KDTree) - можна пропустити для швидкості, якщо не критично
    
    # --- ГОЛОВНИЙ ЦИКЛ ОБРОБКИ ---
    # Перенесемо логіку _iter_polys і _process_one сюди, щоб зафіксувати NaN
    
    def _iter_polys(g):
        if g is None or getattr(g, "is_empty", False): return []
        if getattr(g, "geom_type", "") == "Polygon": return [g]
        if getattr(g, "geom_type", "") == "MultiPolygon": return list(g.geoms)
        return []

    for poly in road_geoms:
        try:
            # 1. Створюємо частини мостів (позитивні)
            parts_to_process = []
            relevant_bridges = [b for b in bridges if b[1] is not None and b[1].intersects(poly)]
            
            for b in relevant_bridges:
                try:
                    b_inter = poly.intersection(b[1])
                    for p in _iter_polys(b_inter):
                        if p.area < 0.1: continue
                        # bridge tuple: (line, area, height, is_water, layer, start, end)
                        parts_to_process.append({
                            'poly': p, 'is_bridge': True, 
                            'height_offset': float(b[2]) * bridge_height_multiplier,
                            'layer': b[4]
                        })
                except: pass

            # 2. Створюємо наземні частини (все інше)
            if bridge_cut_union is not None:
                try:
                    ground_parts = poly.difference(bridge_cut_union)
                except:
                    ground_parts = poly
            else:
                ground_parts = poly

            for p in _iter_polys(ground_parts):
                if p.area < 0.1: continue
                parts_to_process.append({
                    'poly': p, 'is_bridge': False, 'height_offset': 0.0, 'layer': 0
                })

            # Якщо нічого не вийшло (наприклад, bridge_cut_union повністю з'їв полігон, але relevant_bridges порожній через помилку)
            if not parts_to_process:
                # Fallback: малюємо як землю
                parts_to_process.append({'poly': poly, 'is_bridge': False, 'height_offset': 0.0, 'layer': 0})

            # 3. Екструзія кожної частини
            for part in parts_to_process:
                p_poly = part['poly']
                is_br = part['is_bridge']
                h_off = part['height_offset']
                
                # Densify
                p_poly = densify_geometry(p_poly, max_segment_length=10.0)
                if not p_poly.is_valid: p_poly = p_poly.buffer(0)

                # Extrude
                rh = max(float(road_height), 0.1)
                mesh = trimesh.creation.extrude_polygon(p_poly, height=rh)
                
                if mesh is None or len(mesh.vertices) == 0: continue

                if is_br:
                    try: mesh.fix_normals()
                    except: pass

                # Drape on terrain
                if terrain_provider is not None:
                    vertices = mesh.vertices.copy()
                    old_z = vertices[:, 2].copy()
                    
                    # --- FIX NaN VALUE ---
                    ground_z = terrain_provider.get_surface_heights_for_points(vertices[:, :2])
                    if np.any(np.isnan(ground_z)):
                        valid_mask = ~np.isnan(ground_z)
                        fill = np.nanmedian(ground_z[valid_mask]) if np.any(valid_mask) else 0.0
                        ground_z = np.nan_to_num(ground_z, nan=fill)
                    
                    # Logic for elevation
                    if is_br:
                        # Bridge logic
                        base_z = np.median(ground_z) + max(h_off, 6.0) # Мінімум 6м для моста
                        
                        # ANTI-DROWN check (якщо під мостом вода, перевірити чи достатня висота)
                        if hasattr(terrain_provider, 'original_heights_provider') and terrain_provider.original_heights_provider:
                            orig_z = terrain_provider.original_heights_provider.get_heights_for_points(vertices[:, :2])
                            orig_z = np.nan_to_num(orig_z, nan=0.0)
                            water_depth = np.max(orig_z - ground_z)
                            if water_depth > 1.0: # Над водою
                                base_z = max(base_z, np.median(orig_z) + h_off)

                        vertices[:, 2] = base_z + old_z
                        stats['bridge'] += 1
                        
                        # Generate Supports (simplified call)
                        if base_z - np.min(ground_z) > 3.0:
                            try:
                                supps = create_bridge_supports(p_poly, base_z, terrain_provider, None, 30.0, 2.0, 2.0)
                                if supps: road_meshes.extend(supps)
                            except: pass
                    else:
                        # Ground logic
                        vertices[:, 2] = ground_z + old_z
                        stats['ground'] += 1
                        
                        # ANTI-DROWN for ground roads (Pontoon effect)
                        if hasattr(terrain_provider, 'original_heights_provider') and terrain_provider.original_heights_provider:
                            orig_z = terrain_provider.original_heights_provider.get_heights_for_points(vertices[:, :2])
                            orig_z = np.nan_to_num(orig_z, nan=0.0)
                            depth = orig_z - ground_z
                            drown_mask = depth > 0.5
                            if np.any(drown_mask):
                                # Lift exactly to water surface
                                vertices[drown_mask, 2] = orig_z[drown_mask] + old_z[drown_mask] + 0.2
                                stats['anti_drown'] += 1

                    mesh.vertices = vertices

                # Color
                color = [60, 60, 60, 255] if is_br else [40, 40, 40, 255]
                if len(mesh.faces) > 0:
                    mesh.visual = trimesh.visual.ColorVisuals(face_colors=np.tile(color, (len(mesh.faces), 1)))
                
                road_meshes.append(mesh)

        except Exception as e:
            print(f"[WARN] Error processing road poly: {e}")
            continue

    if not road_meshes:
        return None

    print(f"Фіналізація: об'єднання {len(road_meshes)} елементів...")
    print(f"Stats: Bridges={stats['bridge']}, Ground={stats['ground']}, Pontoon Fixes={stats['anti_drown']}")
    
    try:
        combined = trimesh.util.concatenate(road_meshes)
        return combined
    except:
        return road_meshes[0] if road_meshes else None
'''

with open(file_path, 'r', encoding='utf-8') as f:
    lines = f.readlines()

output_lines = []
for line in lines:
    if line.strip().startswith('def process_roads('):
        break
    output_lines.append(line)

final_content = "".join(output_lines) + "\n" + new_process_roads_code

with open(file_path, 'w', encoding='utf-8') as f:
    f.write(final_content)

print("Successfully replaced process_roads function.")
