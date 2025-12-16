"""
Тести для сервісу експорту моделей
"""
import pytest
import trimesh
from services.model_exporter import export_scene


class TestModelExporter:
    """Тести для model_exporter.py"""
    
    def test_export_scene_stl(self, output_dir):
        """Тест експорту сцени у STL"""
        output_file = output_dir / "test.stl"
        box = trimesh.creation.box(extents=[10, 10, 10])
        export_scene(
            terrain_mesh=None,
            road_mesh=None,
            building_meshes=[box],
            water_mesh=None,
            filename=str(output_file),
            format="stl",
            model_size_mm=100.0
        )
        
        assert output_file.exists()
        assert output_file.stat().st_size > 0
    
    def test_export_scene_3mf(self, output_dir):
        """Тест експорту сцени у 3MF"""
        output_file = output_dir / "test.3mf"
        box = trimesh.creation.box(extents=[10, 10, 10])
        export_scene(
            terrain_mesh=None,
            road_mesh=None,
            building_meshes=[box],
            water_mesh=None,
            filename=str(output_file),
            format="3mf",
            model_size_mm=100.0
        )
        assert output_file.exists()
    
    def test_export_scene_with_multiple_objects(self, output_dir):
        """Тест експорту сцени з кількома об'єктами"""
        box1 = trimesh.creation.box(extents=[5, 5, 5])
        box2 = trimesh.creation.box(extents=[3, 3, 3])
        output_file = output_dir / "test_multi.stl"
        export_scene(
            terrain_mesh=None,
            road_mesh=None,
            building_meshes=[box1, box2],
            water_mesh=None,
            filename=str(output_file),
            format="stl",
            model_size_mm=100.0
        )
        
        assert output_file.exists()
    
    def test_export_scene_empty(self, output_dir):
        """Тест експорту порожньої сцени"""
        output_file = output_dir / "test_empty.stl"
        with pytest.raises(Exception):
            export_scene(
                terrain_mesh=None,
                road_mesh=None,
                building_meshes=[],
                water_mesh=None,
                filename=str(output_file),
                format="stl",
                model_size_mm=100.0
            )

