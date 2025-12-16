"""
TerrainProvider - клас для інтерполяції висот рельєфу
Дозволяє отримувати висоту землі в будь-якій точці (X, Y)
"""
import numpy as np
from scipy.interpolate import RegularGridInterpolator
from typing import Optional, Tuple


class TerrainProvider:
    """
    Надає інтерполяцію висот рельєфу для будь-якої точки (X, Y)
    """
    
    def __init__(self, X: np.ndarray, Y: np.ndarray, Z: np.ndarray):
        """
        Ініціалізує TerrainProvider з сіткою висот
        
        Args:
            X: 2D масив X координат (meshgrid)
            Y: 2D масив Y координат (meshgrid)
            Z: 2D масив висот (meshgrid)
        """
        # Витягуємо 1D осі з meshgrid
        self.x_axis = X[0, :] if X.ndim == 2 else X
        self.y_axis = Y[:, 0] if Y.ndim == 2 else Y
        # Зберігаємо сітку висот (2D) — потрібна для інтерполяції, що ТОЧНО відповідає трикутникам terrain mesh
        self.z_grid = Z.astype(float, copy=False)

        # Зберігаємо мінімальну та максимальну висоту для fallback
        self.min_z = float(np.nanmin(Z)) if np.any(~np.isnan(Z)) else 0.0
        self.max_z = float(np.nanmax(Z)) if np.any(~np.isnan(Z)) else 0.0

        # Межі для клампу (щоб уникнути екстраполяції, яка часто "тягне" дороги/будівлі вниз/вгору)
        self.min_x = float(np.min(self.x_axis))
        self.max_x = float(np.max(self.x_axis))
        self.min_y = float(np.min(self.y_axis))
        self.max_y = float(np.max(self.y_axis))
        
        # Створюємо інтерполятор
        # RegularGridInterpolator очікує (y, x) порядок для осей
        self.interpolator = RegularGridInterpolator(
            (self.y_axis, self.x_axis),
            Z,
            bounds_error=False,
            # Критично: не екстраполюємо за межі (fill мінімальною висотою)
            fill_value=self.min_z,
            method='linear'
        )

    def _heights_on_terrain_triangles(self, xs: np.ndarray, ys: np.ndarray) -> np.ndarray:
        """
        Інтерполяція висоти, яка ПОВНІСТЮ збігається з трикутниками terrain mesh.

        Важливо: terrain mesh будується з регулярної сітки і розбиває кожну клітинку
        на два трикутники по діагоналі між bottom_left та top_right (див. create_grid_faces).

        Це прибирає ефект "дороги в текстурі / в повітрі", який з'являється,
        коли draping робиться білінійною інтерполяцією, а рельєф — трикутниками.
        """
        xs = np.clip(xs.astype(float), self.min_x, self.max_x)
        ys = np.clip(ys.astype(float), self.min_y, self.max_y)

        # Індекси клітинки
        j = np.searchsorted(self.x_axis, xs, side="right") - 1
        i = np.searchsorted(self.y_axis, ys, side="right") - 1
        j = np.clip(j, 0, len(self.x_axis) - 2)
        i = np.clip(i, 0, len(self.y_axis) - 2)

        x0 = self.x_axis[j]
        x1 = self.x_axis[j + 1]
        y0 = self.y_axis[i]
        y1 = self.y_axis[i + 1]

        # Нормалізовані координати в межах клітинки [0..1]
        eps = 1e-12
        dx = (xs - x0) / (x1 - x0 + eps)
        dy = (ys - y0) / (y1 - y0 + eps)
        dx = np.clip(dx, 0.0, 1.0)
        dy = np.clip(dy, 0.0, 1.0)

        # Висоти 4-х кутів клітинки
        z00 = self.z_grid[i, j]         # top_left    (dx=0, dy=0)
        z10 = self.z_grid[i, j + 1]     # top_right   (dx=1, dy=0)
        z01 = self.z_grid[i + 1, j]     # bottom_left (dx=0, dy=1)
        z11 = self.z_grid[i + 1, j + 1] # bottom_right(dx=1, dy=1)

        # Трикутники, як у create_grid_faces:
        # T1: top_left (0,0), bottom_left (0,1), top_right (1,0)  => dx + dy <= 1
        # T2: top_right (1,0), bottom_left (0,1), bottom_right (1,1) => dx + dy > 1
        mask = (dx + dy) <= 1.0
        z = np.empty_like(dx, dtype=float)

        # Для T1: z = z00*(1-dx-dy) + z10*dx + z01*dy
        z[mask] = z00[mask] * (1.0 - dx[mask] - dy[mask]) + z10[mask] * dx[mask] + z01[mask] * dy[mask]

        # Для T2: ваги (w11=dx+dy-1, w10=1-dy, w01=1-dx), сума=1
        inv_mask = ~mask
        z[inv_mask] = (
            z11[inv_mask] * (dx[inv_mask] + dy[inv_mask] - 1.0)
            + z10[inv_mask] * (1.0 - dy[inv_mask])
            + z01[inv_mask] * (1.0 - dx[inv_mask])
        )

        # NaN -> min_z
        z = np.where(np.isnan(z), self.min_z, z)
        return z
    
    def get_height_at(self, x: float, y: float) -> float:
        """
        Отримує висоту землі в точці (x, y)
        
        Args:
            x: X координата (схід/захід, easting)
            y: Y координата (північ/південь, northing)
            
        Returns:
            Висота Z в точці (x, y), або мінімальна висота якщо точка за межами
        
        Примітка: RegularGridInterpolator очікує (y, x) порядок для осей,
        але координати передаються як (x, y) де x = схід/захід, y = північ/південь
        """
        try:
            z = self._heights_on_terrain_triangles(np.array([x]), np.array([y]))[0]
            return float(z) if not np.isnan(z) else self.min_z
        except Exception:
            # fallback: старий білінійний інтерполятор
            try:
                x = float(np.clip(x, self.min_x, self.max_x))
                y = float(np.clip(y, self.min_y, self.max_y))
                z = self.interpolator((y, x))
                if z is None or np.isnan(z):
                    return self.min_z
                return float(z)
            except Exception:
                return self.min_z
    
    def get_heights_for_points(self, points: np.ndarray) -> np.ndarray:
        """
        Отримує висоти для масиву точок
        
        Args:
            points: Масив форми (N, 2) з координатами [x, y]
            
        Returns:
            Масив висот форми (N,)
        """
        if len(points) == 0:
            return np.array([])
        
        try:
            xs = points[:, 0].astype(float, copy=False)
            ys = points[:, 1].astype(float, copy=False)
            heights = self._heights_on_terrain_triangles(xs, ys)
            return heights
        except Exception:
            # fallback: старий білінійний інтерполятор
            try:
                xs = np.clip(points[:, 0].astype(float), self.min_x, self.max_x)
                ys = np.clip(points[:, 1].astype(float), self.min_y, self.max_y)
                yx_points = np.column_stack([ys, xs])
                heights = self.interpolator(yx_points)
                heights = np.where(np.isnan(heights), self.min_z, heights)
                return heights
            except Exception:
                return np.full(len(points), self.min_z)
    
    def get_bounds(self) -> Tuple[float, float, float, float]:
        """
        Повертає межі рельєфу (min_x, max_x, min_y, max_y)
        """
        return (
            float(np.min(self.x_axis)),
            float(np.max(self.x_axis)),
            float(np.min(self.y_axis)),
            float(np.max(self.y_axis))
        )

