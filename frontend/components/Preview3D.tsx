"use client";

import { Canvas, useThree } from "@react-three/fiber";
import { OrbitControls, PerspectiveCamera } from "@react-three/drei";
import { Suspense, useEffect, useMemo, useState, useRef } from "react";
import { useGenerationStore } from "@/store/generation-store";
import { api } from "@/lib/api";
import * as THREE from "three";
import { STLLoader } from "three/examples/jsm/loaders/STLLoader.js";
import { ThreeMFLoader } from "three/examples/jsm/loaders/3MFLoader.js";
import JSZip from "jszip";
import { useFrame } from "@react-three/fiber";

function bakeStlZUpToThreeYUp(object: THREE.Object3D) {
  // STL/3MF зазвичай Z-up, а Three.js сцена Y-up.
  // Перетворюємо модель так, щоб вона “лежала” на grid (XZ), і не ставала “стіною”.
  const rot = -Math.PI / 2;

  const bakeMesh = (mesh: THREE.Mesh) => {
    const geom = mesh.geometry as THREE.BufferGeometry | undefined;
    if (!geom) return;
    try {
      geom.rotateX(rot);
      geom.computeBoundingBox();
      geom.computeBoundingSphere();
      // Нормалі після повороту (щоб освітлення було коректним)
      // computeVertexNormals може бути важким на великих мешах, але для STL превʼю це ок.
      geom.computeVertexNormals();
    } catch {
      // ignore
    }
  };

  if (object instanceof THREE.Mesh) {
    bakeMesh(object);
  } else {
    object.traverse((child) => {
      if (child instanceof THREE.Mesh) bakeMesh(child);
    });
  }
}

async function loadStlAsMesh(blob: Blob, color: number): Promise<THREE.Mesh> {
  const url = URL.createObjectURL(blob);
  const loader = new STLLoader();
  return await new Promise<THREE.Mesh>((resolve, reject) => {
    loader.load(
      url,
      (geometry) => {
        URL.revokeObjectURL(url);
        const material = new THREE.MeshStandardMaterial({ color, flatShading: true });
        const mesh = new THREE.Mesh(geometry, material);
        bakeStlZUpToThreeYUp(mesh);
        resolve(mesh);
      },
      undefined,
      (err) => {
        URL.revokeObjectURL(url);
        reject(err);
      }
    );
  });
}

async function loadColoredPartsFromBlobs(blobs: Partial<Record<"base" | "roads" | "buildings" | "water", Blob>>): Promise<THREE.Group> {
  const group = new THREE.Group();
  const colors: Record<string, number> = {
    // Трохи "земляні" кольори, щоб краще читався рельєф
    base: 0x6f7f4a,
    roads: 0x1e1e1e,
    buildings: 0xe3e3e3,
    water: 0x2f6fb8,
    parks: 0x3e8f3e,
    poi: 0xe0b84b,
  };

  const entries = Object.entries(blobs) as Array<[keyof typeof blobs, Blob]>;
  for (const [part, blob] of entries) {
    if (!blob) continue;
    const mesh = await loadStlAsMesh(blob, colors[part as string] ?? 0x888888);
    // mark part type for later preview toggles (e.g. shading)
    (mesh as any).userData = { ...(mesh as any).userData, part };
    const mat = mesh.material as THREE.MeshStandardMaterial;
    // Налаштування матеріалів для кращої читабельності
    if (part === "base") {
      mat.flatShading = false;
      mat.roughness = 1.0;
      mat.metalness = 0.0;
      // ВАЖЛИВО: якщо нормалі бази інколи "перевернуті", з FrontSide вона здається прозорою зверху.
      // Для превʼю робимо DoubleSide.
      mat.side = THREE.DoubleSide;
      mat.needsUpdate = true;
    } else if (part === "buildings") {
      mat.flatShading = false;
      mat.roughness = 0.9;
      mat.metalness = 0.0;
      // Прибирає мерехтіння на стику з землею в превʼю
      mat.polygonOffset = true;
      // Робимо дуже мʼяко, щоб не створювало ілюзію “будівлі висять”.
      mat.polygonOffsetFactor = -0.1;
      mat.polygonOffsetUnits = -1;
      mat.needsUpdate = true;
    } else if (part === "roads") {
      mat.flatShading = true;
      mat.roughness = 0.95;
      mat.metalness = 0.0;
      // Для превʼю: легкий polygonOffset, щоб дороги не "зливалися" з землею (z-fighting),
      // але без агресивних значень (які давали ефект “висять”).
      mat.polygonOffset = true;
      mat.polygonOffsetFactor = -0.1;
      mat.polygonOffsetUnits = -1;
      mat.needsUpdate = true;
    } else if (part === "water") {
      // Вода як "видимий шар": без прозорості, щоб не було “шипів”/стіночок, видимих крізь воду.
      // Для друку вода все одно керується геометрією на бекенді.
      mat.transparent = false;
      mat.opacity = 1.0;
      mat.roughness = 0.3;
      mat.metalness = 0.0;
      mat.needsUpdate = true;
    } else if (part === "parks") {
      mat.flatShading = false;
      mat.roughness = 1.0;
      mat.metalness = 0.0;
      // Prevent z-fighting “thin green lines” on top of terrain in preview
      mat.polygonOffset = true;
      mat.polygonOffsetFactor = -0.2;
      mat.polygonOffsetUnits = -2;
      mat.needsUpdate = true;
    } else if (part === "poi") {
      mat.flatShading = true;
      mat.roughness = 0.9;
      mat.metalness = 0.0;
      mat.needsUpdate = true;
    }
    group.add(mesh);
  }
  return group;
}

// Функція для завантаження 3MF (3MF це ZIP з XML та STL файлами всередині)
async function load3MF(blob: Blob): Promise<THREE.Group> {
  try {
    // ThreeMFLoader потребує URL, але може не працювати з blob URL
    // Спробуємо спочатку через ThreeMFLoader, якщо не вийде - розпакуємо ZIP вручну
    const zipUrl = URL.createObjectURL(blob);

    try {
      return await new Promise<THREE.Group>((resolve, reject) => {
        const loader = new ThreeMFLoader();
        loader.load(
          zipUrl,
          (object) => {
            URL.revokeObjectURL(zipUrl);
            console.log("3MF модель завантажена через ThreeMFLoader");
            console.log("Об'єктів в моделі:", object.children.length);

            // ThreeMFLoader повертає Object3D, обгортаємо в Group
            const group = new THREE.Group();
            group.add(object);

            // Логуємо інформацію про модель
            let totalVertices = 0;
            let totalMeshes = 0;
            group.traverse((child) => {
              if (child instanceof THREE.Mesh) {
                totalMeshes++;
                const geometry = child.geometry;
                if (geometry.attributes.position) {
                  totalVertices += geometry.attributes.position.count;
                }
              }
            });
            console.log("Загальна кількість вершин:", totalVertices, "мешів:", totalMeshes);

            if (totalVertices === 0) {
              reject(new Error("Модель не містить вершин"));
              return;
            }

            resolve(group);
          },
          undefined,
          (error) => {
            URL.revokeObjectURL(zipUrl);
            console.warn("ThreeMFLoader не спрацював, спробуємо розпакувати ZIP:", error);
            reject(error);
          }
        );
      });
    } catch (loaderError: any) {
      URL.revokeObjectURL(zipUrl);
      console.log("ThreeMFLoader не спрацював, розпаковуємо ZIP вручну...");

      // Fallback: розпаковуємо ZIP і шукаємо STL або використовуємо .model файл
      const zip = await JSZip.loadAsync(blob);

      // Шукаємо .model файл
      const modelFile = zip.file("3D/3dmodel.model");
      if (!modelFile) {
        throw new Error("Не знайдено файл 3D/3dmodel.model в 3MF");
      }

      const modelBlob = await modelFile.async('blob');

      // Перевіряємо, чи це XML (3MF формат) або бінарний (STL)
      const firstBytes = await modelBlob.slice(0, 50).text();
      const isXML = firstBytes.trim().startsWith('<?xml') || firstBytes.trim().startsWith('<model');

      if (isXML) {
        // Це XML 3MF - потрібен парсер, але наразі використаємо fallback
        throw new Error("XML 3MF формат потребує спеціального парсера. Використовуйте STL формат або встановіть 3MF парсер.");
      } else {
        // Це бінарний STL - завантажуємо напряму
        const stlUrl = URL.createObjectURL(modelBlob);
        return new Promise((resolve, reject) => {
          const loader = new STLLoader();
          loader.load(
            stlUrl,
            (geometry) => {
              URL.revokeObjectURL(stlUrl);
              const material = new THREE.MeshStandardMaterial({
                color: 0x888888,
                flatShading: true
              });
              const mesh = new THREE.Mesh(geometry, material);
              bakeStlZUpToThreeYUp(mesh);
              const group = new THREE.Group();
              group.add(mesh);
              resolve(group);
            },
            undefined,
            (error) => {
              URL.revokeObjectURL(stlUrl);
              console.error("Помилка завантаження STL:", error);
              reject(error);
            }
          );
        });
      }
    }
  } catch (error: any) {
    console.error("Помилка обробки 3MF:", error);
    throw error;
  }
}

// Компонент для автоматичного позиціювання камери
function CameraController() {
  const { downloadUrl, showAllZones, taskIds } = useGenerationStore();
  const cameraRef = useRef<THREE.PerspectiveCamera | null>(null);

  useEffect(() => {
    // Налаштовуємо камеру для кращого перегляду
    const timer = setTimeout(() => {
      if (cameraRef.current) {
        // Для batch preview (всі зони) - більша відстань для кращого огляду
        if (showAllZones && taskIds && taskIds.length > 1) {
          const zoneCount = taskIds.length;
          // Відстань залежить від кількості зон (більше зон = більша відстань)
          const baseDistance = 300;
          const distanceMultiplier = Math.max(1, Math.sqrt(zoneCount) * 0.5);
          const distance = baseDistance * distanceMultiplier;
          cameraRef.current.position.set(distance, distance * 0.8, distance);
          cameraRef.current.lookAt(0, 0, 0);
          console.log(`Камера налаштована для batch preview (${zoneCount} зон):`, cameraRef.current.position);
        } else {
          // Для однієї зони - стандартна відстань
          const distance = 300;
          cameraRef.current.position.set(distance, distance, distance);
          cameraRef.current.lookAt(0, 0, 0);
          console.log("Камера налаштована для однієї зони:", cameraRef.current.position);
        }
        cameraRef.current.updateProjectionMatrix();
      }
    }, 100);

    return () => clearTimeout(timer);
  }, [downloadUrl, showAllZones, taskIds]);

  return (
    <PerspectiveCamera
      ref={cameraRef}
      makeDefault
      position={[300, 300, 300]}
      fov={50}
      near={0.1}
      far={2000}
    />
  );
}

type RotateMode = "camera" | "model";
type CameraMode = "orbit" | "fly";

function FreeFlyControls({
  enabled,
  speed,
  onSpeedChange,
}: {
  enabled: boolean;
  speed: number;
  onSpeedChange: (v: number) => void;
}) {
  const { camera, gl } = useThree();
  const stateRef = useRef({
    keys: new Set<string>(),
    mouseDown: false,
    yaw: 0,
    pitch: 0,
    speed: 120, // units/sec (preview space)
    boost: 3.0,
    sensitivity: 0.0025,
  });

  const tmpForward = useMemo(() => new THREE.Vector3(), []);
  const tmpRight = useMemo(() => new THREE.Vector3(), []);
  const tmpUp = useMemo(() => new THREE.Vector3(0, 1, 0), []);
  const tmpMove = useMemo(() => new THREE.Vector3(), []);
  const euler = useMemo(() => new THREE.Euler(0, 0, 0, "YXZ"), []);

  // Initialize yaw/pitch from camera when enabling
  useEffect(() => {
    if (!enabled) return;
    // Sync external speed setting into control state
    stateRef.current.speed = Math.max(10, Math.min(800, Number(speed) || 120));
    const q = camera.quaternion.clone();
    euler.setFromQuaternion(q, "YXZ");
    stateRef.current.yaw = euler.y;
    stateRef.current.pitch = euler.x;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled, speed]);

  useEffect(() => {
    if (!enabled) return;

    const el = gl.domElement;

    const onKeyDown = (e: KeyboardEvent) => {
      stateRef.current.keys.add(e.code);
      // Prevent page scroll when using arrows/space
      if (["Space", "ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight"].includes(e.code)) {
        e.preventDefault();
      }
    };
    const onKeyUp = (e: KeyboardEvent) => {
      stateRef.current.keys.delete(e.code);
    };
    const onMouseDown = (e: MouseEvent) => {
      // Right click or middle click enables look-around while held
      if (e.button === 2 || e.button === 1) {
        stateRef.current.mouseDown = true;
        e.preventDefault();
      }
    };
    const onMouseUp = (e: MouseEvent) => {
      if (e.button === 2 || e.button === 1) {
        stateRef.current.mouseDown = false;
        e.preventDefault();
      }
    };
    const onMouseMove = (e: MouseEvent) => {
      if (!stateRef.current.mouseDown) return;
      const s = stateRef.current;
      s.yaw -= e.movementX * s.sensitivity;
      s.pitch -= e.movementY * s.sensitivity;
      // Clamp pitch to avoid flipping
      const lim = Math.PI / 2 - 0.01;
      s.pitch = Math.max(-lim, Math.min(lim, s.pitch));
    };
    const onWheel = (e: WheelEvent) => {
      // Adjust speed with wheel (no page scroll when hovering canvas)
      const s = stateRef.current;
      const delta = Math.sign(e.deltaY);
      s.speed = Math.max(10, Math.min(800, s.speed * (delta > 0 ? 0.9 : 1.1)));
      onSpeedChange(s.speed);
      e.preventDefault();
    };
    const onContextMenu = (e: MouseEvent) => {
      // Disable context menu on canvas so RMB is usable
      e.preventDefault();
    };

    window.addEventListener("keydown", onKeyDown, { passive: false });
    window.addEventListener("keyup", onKeyUp);
    el.addEventListener("mousedown", onMouseDown);
    window.addEventListener("mouseup", onMouseUp);
    window.addEventListener("mousemove", onMouseMove);
    el.addEventListener("wheel", onWheel, { passive: false });
    el.addEventListener("contextmenu", onContextMenu);

    return () => {
      window.removeEventListener("keydown", onKeyDown as any);
      window.removeEventListener("keyup", onKeyUp as any);
      el.removeEventListener("mousedown", onMouseDown as any);
      window.removeEventListener("mouseup", onMouseUp as any);
      window.removeEventListener("mousemove", onMouseMove as any);
      el.removeEventListener("wheel", onWheel as any);
      el.removeEventListener("contextmenu", onContextMenu as any);
      stateRef.current.keys.clear();
      stateRef.current.mouseDown = false;
    };
  }, [enabled, gl.domElement]);

  useFrame((_, delta) => {
    if (!enabled) return;

    const s = stateRef.current;

    // Update camera rotation
    euler.set(s.pitch, s.yaw, 0);
    camera.quaternion.setFromEuler(euler);

    // Movement
    const keys = s.keys;
    const boost = keys.has("ShiftLeft") || keys.has("ShiftRight") ? s.boost : 1.0;
    const v = s.speed * boost * Math.min(delta, 0.05);

    tmpMove.set(0, 0, 0);

    // Forward is -Z in camera space
    tmpForward.set(0, 0, -1).applyQuaternion(camera.quaternion);
    tmpRight.set(1, 0, 0).applyQuaternion(camera.quaternion);

    // Optional: keep forward movement mostly horizontal for easier navigation
    tmpForward.y = 0;
    tmpRight.y = 0;
    tmpForward.normalize();
    tmpRight.normalize();

    if (keys.has("KeyW")) tmpMove.addScaledVector(tmpForward, v);
    if (keys.has("KeyS")) tmpMove.addScaledVector(tmpForward, -v);
    if (keys.has("KeyA")) tmpMove.addScaledVector(tmpRight, -v);
    if (keys.has("KeyD")) tmpMove.addScaledVector(tmpRight, v);
    if (keys.has("KeyE")) tmpMove.addScaledVector(tmpUp, v);
    if (keys.has("KeyQ")) tmpMove.addScaledVector(tmpUp, -v);

    if (tmpMove.lengthSq() > 0) {
      camera.position.add(tmpMove);
      camera.updateMatrixWorld();
    }
  });

  return null;
}

function ModelLoader({ rotateMode }: { rotateMode: RotateMode }) {
  const { downloadUrl, activeTaskId, exportFormat, showAllZones, taskIds, taskStatuses, batchZoneMetaByTaskId, terrainSmoothShading } = useGenerationStore();
  const [model, setModel] = useState<THREE.Group | THREE.Mesh | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [hasLoadedTestModel, setHasLoadedTestModel] = useState(false);

  // Керування поворотом моделі (а не камери)
  const modelGroupRef = useRef<THREE.Group | null>(null);
  const dragRef = useRef<{ dragging: boolean; x: number; y: number }>({ dragging: false, x: 0, y: 0 });

  const resetModelRotation = () => {
    if (modelGroupRef.current) {
      modelGroupRef.current.rotation.set(0, 0, 0);
    }
  };

  // Завантажуємо тестову модель при старті
  useEffect(() => {
    if (showAllZones) return;
    if (hasLoadedTestModel || downloadUrl) return;

    const loadTestModel = async () => {
      setLoading(true);
      try {
        console.log("=== Завантаження тестової моделі (кольорові частини) ===");
        // 1) Пробуємо маніфест частин
        let loadedModel: THREE.Group | THREE.Mesh;
        try {
          const mResp = await fetch("http://localhost:8000/api/test-model/manifest");
          if (mResp.ok) {
            const json = await mResp.json();
            const parts = json?.parts || {};
            const blobs: any = {};
            const fetchPart = async (p: "base" | "roads" | "buildings" | "water" | "parks" | "poi") => {
              const url = parts[p];
              if (!url) return;
              const r = await fetch(`http://localhost:8000${url}`);
              if (r.ok) blobs[p] = await r.blob();
            };
            await Promise.all([
              fetchPart("base"),
              fetchPart("roads"),
              fetchPart("buildings"),
              fetchPart("water"),
              fetchPart("parks"),
              fetchPart("poi"),
            ]);
            if (Object.keys(blobs).length > 0) {
              loadedModel = await loadColoredPartsFromBlobs(blobs);
            } else {
              throw new Error("Маніфест є, але частини не завантажились");
            }
          } else {
            throw new Error("Маніфест не доступний");
          }
        } catch (e) {
          // 2) Fallback на старий endpoint
          const response = await fetch("http://localhost:8000/api/test-model");
          if (!response.ok) {
            console.warn("Тестова модель не знайдена (404), пропускаємо");
            setLoading(false);
            return;
          }
          const blob = await response.blob();
          loadedModel = await loadStlAsMesh(blob, 0x888888);
        }

        // ВАЖЛИВО: для стабільного превʼю не центруємо по "висоті".
        // Ми ставимо модель на "підлогу" (minY=0) і центруємо тільки X/Z.
        // Інакше камера часто опиняється під моделлю, а обʼєкти виглядають так, ніби "висять".
        loadedModel.position.set(0, 0, 0);
        loadedModel.scale.set(1, 1, 1);
        loadedModel.updateMatrixWorld(true);

        const box = new THREE.Box3().setFromObject(loadedModel);
        const size = box.getSize(new THREE.Vector3());
        const maxDim = Math.max(size.x, size.y, size.z);



        if (maxDim > 0) {
          const targetSize = maxDim < 0.1 ? 300 : 220;
          const viewScale = targetSize / maxDim;
          loadedModel.scale.set(viewScale, viewScale, viewScale);
          loadedModel.updateMatrixWorld(true);

          const boxAfter = new THREE.Box3().setFromObject(loadedModel);
          const center = boxAfter.getCenter(new THREE.Vector3());
          const min = boxAfter.min.clone();

          loadedModel.position.x -= center.x;
          loadedModel.position.z -= center.z;
          loadedModel.position.y -= min.y;
          loadedModel.updateMatrixWorld(true);
        } else {
          console.error("❌ Модель має нульовий розмір!");
        }

        console.log("✅ Тестова модель готова до відображення");
        setModel(loadedModel);
        setHasLoadedTestModel(true);
        setLoading(false);
      } catch (err: any) {
        console.warn("Не вдалося завантажити тестову модель:", err);
        setLoading(false);
      }
    };

    loadTestModel();
  }, [hasLoadedTestModel, downloadUrl]);

  const normalizeModelForPreview = (obj: THREE.Object3D) => {
    obj.position.set(0, 0, 0);
    obj.scale.set(1, 1, 1);
    obj.updateMatrixWorld(true);

    const box = new THREE.Box3().setFromObject(obj);
    const center = box.getCenter(new THREE.Vector3());
    const min = box.min.clone();

    obj.position.x -= center.x;
    obj.position.z -= center.z;
    obj.position.y -= min.y;
    obj.updateMatrixWorld(true);

    const boxAfter = new THREE.Box3().setFromObject(obj);
    const sizeAfter = boxAfter.getSize(new THREE.Vector3());
    const maxDim = Math.max(sizeAfter.x, sizeAfter.y, sizeAfter.z);

    return { size: sizeAfter, maxDim };
  };

  const loadZoneModel = async (id: string) => {
    // 1) Кольорові частини: пробуємо завантажити STL по частинах
    let loadedModel: THREE.Group | THREE.Mesh;
    const blobs: any = {};
    const tryPart = async (p: "base" | "roads" | "buildings" | "water" | "parks" | "poi") => {
      try {
        const b = await api.downloadModel(id, "stl", p);
        if (b && b.size > 100) blobs[p] = b;
      } catch {
        // ignore
      }
    };
    await Promise.all([
      tryPart("base"),
      tryPart("roads"),
      tryPart("buildings"),
      tryPart("water"),
      tryPart("parks"),
      tryPart("poi"),
    ]);

    if (Object.keys(blobs).length > 0) {
      loadedModel = await loadColoredPartsFromBlobs(blobs);
    } else {
      const blob = await api.downloadModel(id, "stl");
      loadedModel = await loadStlAsMesh(blob, 0x888888);
    }

    const info = normalizeModelForPreview(loadedModel);
    return { id, obj: loadedModel, ...info };
  };

  // Batch preview:
  // - If tiles are exported in global XY (stitching mode), we should NOT normalize each tile individually,
  //   and we should NOT do artificial grid layout. Just load as-is and normalize the whole group once.
  // - If tiles are still centered (legacy), we fallback to the old grid layout so user can still see all zones.
  useEffect(() => {
    if (!showAllZones) return;
    if (!taskIds || taskIds.length < 2) return;

    const completedIds = taskIds.filter((id) => (taskStatuses as any)?.[id]?.status === "completed");
    const idsToLoad = completedIds.length ? completedIds : [];
    if (idsToLoad.length === 0) {
      // ще нічого не готово
      setModel(null);
      return;
    }

    const run = async () => {
      setLoading(true);
      setError(null);
      try {
        // Load without per-tile normalize so we can preserve real relative alignment (when available)
        const loadZoneModelRaw = async (id: string) => {
          let loadedModel: THREE.Group | THREE.Mesh;
          const blobs: any = {};
          const tryPart = async (p: "base" | "roads" | "buildings" | "water" | "parks" | "poi") => {
            try {
              const b = await api.downloadModel(id, "stl", p);
              if (b && b.size > 100) blobs[p] = b;
            } catch {
              // ignore
            }
          };
          await Promise.all([
            tryPart("base"),
            tryPart("roads"),
            tryPart("buildings"),
            tryPart("water"),
            tryPart("parks"),
            tryPart("poi"),
          ]);

          if (Object.keys(blobs).length > 0) {
            loadedModel = await loadColoredPartsFromBlobs(blobs);
          } else {
            const blob = await api.downloadModel(id, "stl");
            loadedModel = await loadStlAsMesh(blob, 0x888888);
          }

          loadedModel.updateMatrixWorld(true);
          return { id, obj: loadedModel };
        };

        const models = (await Promise.all(idsToLoad.map((id) => loadZoneModelRaw(id)))).filter(Boolean) as any[];
        if (!models.length) {
          setModel(null);
          setLoading(false);
          return;
        }

        const group = new THREE.Group();
        for (const m of models) {
          m.obj.updateMatrixWorld(true);
          group.add(m.obj);
        }

        // Decide whether tiles already have meaningful relative positions.
        // If all centers are ~the same => legacy centered exports => use grid layout.
        const centers = models.map((m) => new THREE.Box3().setFromObject(m.obj).getCenter(new THREE.Vector3()));
        const mean = centers.reduce((acc, c) => acc.add(c), new THREE.Vector3()).multiplyScalar(1 / Math.max(1, centers.length));
        const spread = centers.reduce((acc, c) => acc + c.clone().sub(mean).length(), 0) / Math.max(1, centers.length);
        const looksGlobal = spread > 1.0; // >1 unit spread => not all centered at origin

        if (!looksGlobal) {
          // Legacy layout fallback (keep previous behavior)
          const zoneInfo = models.map((m) => {
            const box = new THREE.Box3().setFromObject(m.obj);
            return {
              size: box.getSize(new THREE.Vector3()),
              center: box.getCenter(new THREE.Vector3()),
              min: box.min.clone(),
              model: m
            };
          });

          const metaByTaskId = batchZoneMetaByTaskId || {};
          const canUseMapLayout = models.every((m) => {
            const meta = (metaByTaskId as any)[m.id];
            return meta && (meta.row != null || meta.col != null);
          });

          if (canUseMapLayout) {
            const rows = models.map((m) => Number((metaByTaskId as any)[m.id].row ?? 0));
            const cols = models.map((m) => Number((metaByTaskId as any)[m.id].col ?? 0));
            const minRow = Math.min(...rows);
            const minCol = Math.min(...cols);

            const maxW = Math.max(...zoneInfo.map((z) => z.size.x));
            const maxD = Math.max(...zoneInfo.map((z) => z.size.z));
            const stepX = maxW * 1.0;
            const stepZ = maxD * 1.0;

            zoneInfo.forEach((item) => {
              const meta = (metaByTaskId as any)[item.model.id] || {};
              const r = Number(meta.row ?? 0) - minRow;
              const c = Number(meta.col ?? 0) - minCol;
              const xShift = (r % 2) ? stepX * 0.5 : 0.0;

              item.model.obj.position.x = c * stepX + xShift - item.center.x;
              item.model.obj.position.z = r * stepZ - item.center.z;
              item.model.obj.position.y = -item.min.y;
              item.model.obj.updateMatrixWorld(true);
            });
          } else {
            // Fallback: Simple grid layout based on index if no row/col meta
            console.warn("Batch preview: No row/col metadata found, using fallback grid layout");
            const count = zoneInfo.length;
            const cols = Math.ceil(Math.sqrt(count));
            const maxW = Math.max(...zoneInfo.map((z) => z.size.x));
            const maxD = Math.max(...zoneInfo.map((z) => z.size.z));
            const padding = 20; // mm

            zoneInfo.forEach((item, index) => {
              const r = Math.floor(index / cols);
              const c = index % cols;

              item.model.obj.position.x = c * (maxW + padding) - item.center.x;
              item.model.obj.position.z = r * (maxD + padding) - item.center.z;
              item.model.obj.position.y = -item.min.y;
              item.model.obj.updateMatrixWorld(true);
            });
          }
        }

        // 5. Центруємо всю групу
        const groupBox = new THREE.Box3().setFromObject(group);
        const gCenter = groupBox.getCenter(new THREE.Vector3());
        const gMin = groupBox.min.clone();
        group.position.x -= gCenter.x;
        group.position.z -= gCenter.z;
        group.position.y -= gMin.y;
        group.updateMatrixWorld(true);

        // Додаємо легкі візуальні індикатори для кожної зони (опціонально)
        // Для продуктивності не додаємо складні об'єкти, але зберігаємо інформацію

        (group as any).userData = { batch: true, ids: idsToLoad, zoneCount: idsToLoad.length };
        setModel(group);
      } catch (e: any) {
        setError(e?.message || "Помилка завантаження batch превʼю");
      } finally {
        setLoading(false);
      }
    };

    run();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [showAllZones, taskIds.join(","), JSON.stringify(taskStatuses)]);

  useEffect(() => {
    if (showAllZones) return;
    // ВАЖЛИВО: Не скидаємо модель, якщо вона вже завантажена
    // Модель може зникнути, якщо downloadUrl тимчасово стає null під час оновлення стану
    if (!downloadUrl || !activeTaskId) {
      // Не скидаємо модель, якщо вже завантажена тестова або інша модель
      // Це запобігає зникненню моделі під час оновлення стану
      if (!hasLoadedTestModel && !model) {
        // Тільки якщо немає ні тестової моделі, ні завантаженої моделі
        setModel(null);
        setError(null);
      }
      return;
    }

    // Якщо модель вже завантажена для цього taskId, не перезавантажуємо
    const currentTaskId = (model as any)?.userData?.taskId;
    if (model && currentTaskId === activeTaskId) {
      console.log("Модель вже завантажена для цього taskId, пропускаємо перезавантаження");
      return;
    }

    // Якщо завантажуємо нову модель, скидаємо попередню (якщо вона не тестова)
    if (model && !hasLoadedTestModel) {
      console.log("Завантажуємо нову модель, скидаємо попередню");
      setModel(null);
    }

    const loadModel = async () => {
      setLoading(true);
      setError(null);
      try {
        console.log("Завантаження моделі...", { taskId: activeTaskId, downloadUrl, exportFormat });

        // 1) Кольорові частини: пробуємо завантажити STL по частинах
        let loadedModel: THREE.Group | THREE.Mesh;
        const blobs: any = {};
        const tryPart = async (p: "base" | "roads" | "buildings" | "water" | "parks" | "poi") => {
          try {
            const b = await api.downloadModel(activeTaskId, "stl", p);
            if (b && b.size > 100) blobs[p] = b;
          } catch {
            // ignore missing part
          }
        };
        await Promise.all([
          tryPart("base"),
          tryPart("roads"),
          tryPart("buildings"),
          tryPart("water"),
          tryPart("parks"),
          tryPart("poi"),
        ]);

        if (Object.keys(blobs).length > 0) {
          loadedModel = await loadColoredPartsFromBlobs(blobs);
        } else {
          // 2) Fallback на один STL
          const blob = await api.downloadModel(activeTaskId, "stl");
          loadedModel = await loadStlAsMesh(blob, 0x888888);
        }

        // Стабільні трансформації для превʼю:
        // - масштабуємо під камеру
        // - центруємо лише X/Z
        // - ставимо на "підлогу" (minY=0)
        loadedModel.position.set(0, 0, 0);
        loadedModel.scale.set(1, 1, 1);
        loadedModel.updateMatrixWorld(true);

        const box = new THREE.Box3().setFromObject(loadedModel);
        const size = box.getSize(new THREE.Vector3());
        const maxDim = Math.max(size.x, size.y, size.z);

        console.log("Розміри моделі до обробки:", size.x, size.y, size.z);
        console.log("Максимальний розмір:", maxDim);

        if (maxDim === 0) {
          throw new Error("Модель має нульовий розмір");
        }

        const targetSize = maxDim < 0.1 ? 300 : 220;
        const viewScale = targetSize / maxDim;
        loadedModel.scale.set(viewScale, viewScale, viewScale);
        loadedModel.updateMatrixWorld(true);

        const boxAfter = new THREE.Box3().setFromObject(loadedModel);
        const center = boxAfter.getCenter(new THREE.Vector3());
        const min = boxAfter.min.clone();

        loadedModel.position.x -= center.x;
        loadedModel.position.z -= center.z;
        loadedModel.position.y -= min.y;
        loadedModel.updateMatrixWorld(true);

        // Перевіряємо розміри після обробки
        const boxFinal = new THREE.Box3().setFromObject(loadedModel);
        const sizeAfter = boxFinal.getSize(new THREE.Vector3());
        const centerAfter = boxFinal.getCenter(new THREE.Vector3());
        console.log("Розміри моделі після обробки:", sizeAfter.x, sizeAfter.y, sizeAfter.z);
        console.log("Центр моделі після обробки:", centerAfter.x, centerAfter.y, centerAfter.z);
        console.log("Модель успішно завантажена та оброблена");

        // Зберігаємо інформацію про модель для налаштування камери
        (loadedModel as any).userData = {
          size: sizeAfter,
          center: centerAfter,
          maxDim: Math.max(sizeAfter.x, sizeAfter.y, sizeAfter.z),
          taskId: activeTaskId,  // Зберігаємо taskId, щоб не перезавантажувати
          exportFormat: exportFormat
        };

        console.log("✅ Модель готова до відображення, встановлюємо в state");
        setModel(loadedModel);
        setLoading(false);
        console.log("✅ Модель встановлена в state, має відображатися");
      } catch (error: any) {
        console.error("Помилка завантаження моделі:", error);
        setError(error.message || "Помилка завантаження моделі");
        setLoading(false);
      }
    };

    loadModel();
  }, [downloadUrl, activeTaskId, exportFormat, showAllZones]);

  // Terrain shading toggle: seam lines on slopes are often just normal discontinuity between separate tiles.
  useEffect(() => {
    if (!model) return;
    (model as any).traverse?.((child: any) => {
      if (!(child instanceof THREE.Mesh)) return;
      if (child.userData?.part !== "base") return;
      const mat = child.material as THREE.MeshStandardMaterial | undefined;
      if (!mat) return;
      // smooth shading = vertex normals; can show a seam line between separate meshes
      mat.flatShading = !terrainSmoothShading;
      mat.needsUpdate = true;
      try {
        (child.geometry as THREE.BufferGeometry | undefined)?.computeVertexNormals();
      } catch {
        // ignore
      }
    });
  }, [model, terrainSmoothShading]);

  if (loading) {
    return (
      <>
        <ambientLight intensity={0.5} />
        <directionalLight position={[10, 10, 5]} intensity={1} />
        <gridHelper args={[100, 100]} />
        <axesHelper args={[50]} />
        <mesh>
          <boxGeometry args={[10, 10, 10]} />
          <meshStandardMaterial color="orange" />
        </mesh>
      </>
    );
  }

  if (error) {
    console.error("Помилка в ModelLoader:", error);
    return (
      <>
        <ambientLight intensity={0.5} />
        <directionalLight position={[10, 10, 5]} intensity={1} />
        <gridHelper args={[100, 100]} />
        <axesHelper args={[50]} />
        <mesh>
          <boxGeometry args={[10, 10, 10]} />
          <meshStandardMaterial color="red" />
        </mesh>
      </>
    );
  }

  if (!model) {
    console.log("ModelLoader: модель не завантажена, показуємо placeholder");
    return (
      <>
        <ambientLight intensity={0.8} />
        <directionalLight position={[100, 100, 100]} intensity={1.0} />
        <directionalLight position={[-100, -100, -100]} intensity={0.5} />
        <gridHelper args={[200, 20]} />
        <axesHelper args={[100]} />
        <mesh position={[0, 0, 0]}>
          <boxGeometry args={[20, 20, 20]} />
          <meshStandardMaterial color="orange" />
        </mesh>
      </>
    );
  }

  console.log("ModelLoader: відображаємо модель", model);
  console.log("ModelLoader: downloadUrl:", downloadUrl, "taskId:", activeTaskId, "loading:", loading, "error:", error);

  // Перевіряємо, чи модель має геометрію
  let hasGeometry = false;
  let vertexCount = 0;
  if (model instanceof THREE.Group) {
    model.traverse((child) => {
      if (child instanceof THREE.Mesh && child.geometry) {
        hasGeometry = true;
        if (child.geometry.attributes.position) {
          vertexCount += child.geometry.attributes.position.count;
        }
      }
    });
  } else if (model instanceof THREE.Mesh && model.geometry) {
    hasGeometry = true;
    if (model.geometry.attributes.position) {
      vertexCount = model.geometry.attributes.position.count;
    }
  }

  console.log("ModelLoader: модель має геометрію:", hasGeometry, "вершин:", vertexCount);

  if (!hasGeometry || vertexCount === 0) {
    console.warn("⚠️ Модель не містить геометрії або має 0 вершин!");
    // Не повертаємо null, щоб не зникнути - показуємо placeholder
    return (
      <>
        <ambientLight intensity={0.8} />
        <directionalLight position={[100, 100, 100]} intensity={1.0} />
        <directionalLight position={[-100, -100, -100]} intensity={0.5} />
        <gridHelper args={[200, 20]} />
        <axesHelper args={[100]} />
        <mesh position={[0, 0, 0]}>
          <boxGeometry args={[20, 20, 20]} />
          <meshStandardMaterial color="red" />
        </mesh>
      </>
    );
  }

  // В режимі rotateMode="model" — drag миші крутить модель (а не камеру)
  const onPointerDown = (e: any) => {
    if (rotateMode !== "model") return;
    e.stopPropagation();
    dragRef.current.dragging = true;
    dragRef.current.x = e.clientX;
    dragRef.current.y = e.clientY;
  };

  const onPointerUp = (e: any) => {
    if (rotateMode !== "model") return;
    e.stopPropagation();
    dragRef.current.dragging = false;
  };

  const onPointerMove = (e: any) => {
    if (rotateMode !== "model") return;
    if (!dragRef.current.dragging) return;
    e.stopPropagation();
    const dx = e.clientX - dragRef.current.x;
    const dy = e.clientY - dragRef.current.y;
    dragRef.current.x = e.clientX;
    dragRef.current.y = e.clientY;

    const group = modelGroupRef.current;
    if (!group) return;

    // Чутливість
    const speed = 0.01;
    group.rotation.y += dx * speed;
    group.rotation.x += dy * speed;
  };

  return (
    <>
      <ambientLight intensity={0.55} />
      <hemisphereLight args={[0xffffff, 0x2b2b2b, 0.65]} />
      <directionalLight position={[200, 250, 150]} intensity={1.0} />
      <directionalLight position={[-200, -150, -100]} intensity={0.35} />
      {/* Обгортаємо модель у Group, щоб можна було крутити саме модель */}
      <group
        ref={modelGroupRef}
        onPointerDown={onPointerDown}
        onPointerUp={onPointerUp}
        onPointerLeave={onPointerUp}
        onPointerMove={onPointerMove}
        onDoubleClick={(e) => {
          if (rotateMode !== "model") return;
          e.stopPropagation();
          resetModelRotation();
        }}
      >
        <primitive object={model} />
      </group>
    </>
  );
}

export function Preview3D() {
  const { downloadUrl, isGenerating, progress, terrainSmoothShading, setTerrainSmoothShading } = useGenerationStore();
  const [gridVisible, setGridVisible] = useState(true);
  const [axesVisible, setAxesVisible] = useState(true);
  const [rotateMode, setRotateMode] = useState<RotateMode>("camera");
  const [cameraMode, setCameraMode] = useState<CameraMode>("orbit");
  const [flySpeed, setFlySpeed] = useState<number>(120);

  return (
    <div className="w-full h-full bg-gray-900 relative" style={{ minHeight: '100%' }}>
      {/* UI overlay */}
      <div className="absolute top-3 right-3 z-20 flex flex-col gap-2 pointer-events-auto">
        <div className="bg-black/60 text-white rounded px-3 py-2 text-xs space-y-2">
          <div className="flex items-center justify-between gap-3">
            <span>Grid</span>
            <button
              className="px-2 py-1 rounded bg-white/10 hover:bg-white/20"
              onClick={() => setGridVisible((v) => !v)}
            >
              {gridVisible ? "Hide" : "Show"}
            </button>
          </div>
          <div className="flex items-center justify-between gap-3">
            <span>Axes</span>
            <button
              className="px-2 py-1 rounded bg-white/10 hover:bg-white/20"
              onClick={() => setAxesVisible((v) => !v)}
            >
              {axesVisible ? "Hide" : "Show"}
            </button>
          </div>
          <div className="flex items-center justify-between gap-3">
            <span>Rotate</span>
            <button
              className="px-2 py-1 rounded bg-white/10 hover:bg-white/20"
              onClick={() => setRotateMode((m) => (m === "camera" ? "model" : "camera"))}
              title="Camera: крутиться камера (OrbitControls). Model: крутиться сама модель (drag по моделі)."
            >
              {rotateMode === "camera" ? "Camera" : "Model"}
            </button>
          </div>
          <div className="flex items-center justify-between gap-3">
            <span>Camera</span>
            <button
              className="px-2 py-1 rounded bg-white/10 hover:bg-white/20"
              onClick={() => setCameraMode((m) => (m === "orbit" ? "fly" : "orbit"))}
              title="Orbit: стандартний огляд. Fly: вільний політ (WASD, Q/E, Shift, RMB+mouse)."
            >
              {cameraMode === "orbit" ? "Orbit" : "Fly"}
            </button>
          </div>
          <div className="text-[10px] text-white/70">
            {cameraMode === "fly"
              ? "Fly: WASD рух, Q/E вгору/вниз, Shift швидше, RMB+mouse дивитись, wheel = speed."
              : (rotateMode === "model" ? "Drag по моделі = rotate. Double-click = reset." : "Drag = rotate camera.")}
          </div>
          {cameraMode === "fly" && (
            <div className="pt-1">
              <div className="flex items-center justify-between gap-3">
                <span>Fly speed</span>
                <span className="text-[10px] text-white/70 tabular-nums">{Math.round(flySpeed)}</span>
              </div>
              <input
                className="w-full"
                type="range"
                min={10}
                max={800}
                step={5}
                value={flySpeed}
                onChange={(e) => setFlySpeed(Number(e.target.value))}
              />
            </div>
          )}
          <div className="flex items-center justify-between gap-3 pt-1">
            <span>Terrain</span>
            <button
              className="px-2 py-1 rounded bg-white/10 hover:bg-white/20"
              onClick={() => setTerrainSmoothShading(!terrainSmoothShading)}
              title="Smooth = плавні нормалі (може бути видимий шов між тайлами). Flat = шов майже не видно (але видно грані)."
            >
              {terrainSmoothShading ? "Smooth" : "Flat"}
            </button>
          </div>
        </div>
      </div>
      {isGenerating && (
        <div className="absolute inset-0 flex items-center justify-center text-white z-10 pointer-events-none">
          <div className="text-center">
            <p className="text-lg mb-2">Генерація моделі...</p>
            <p className="text-sm text-gray-400">{progress}%</p>
          </div>
        </div>
      )}
      <Canvas style={{ width: '100%', height: '100%', display: 'block' }}>
        <Suspense fallback={null}>
          <CameraController />
          <FreeFlyControls enabled={cameraMode === "fly"} speed={flySpeed} onSpeedChange={setFlySpeed} />
          <OrbitControls
            enabled={cameraMode === "orbit"}
            enableDamping
            dampingFactor={0.05}
            minDistance={10}
            maxDistance={2000}
            target={[0, 0, 0]}
            autoRotate={false}
            enableRotate={rotateMode === "camera"}
          />
          {gridVisible && <gridHelper args={[200, 20]} />}
          {axesVisible && <axesHelper args={[100]} />}
          <ModelLoader rotateMode={rotateMode} />
        </Suspense>
      </Canvas>
    </div>
  );
}
