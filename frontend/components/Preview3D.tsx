"use client";

import { Canvas, useThree } from "@react-three/fiber";
import { OrbitControls, PerspectiveCamera } from "@react-three/drei";
import { Suspense, useEffect, useState, useRef } from "react";
import { useGenerationStore } from "@/store/generation-store";
import { api } from "@/lib/api";
import * as THREE from "three";
import { STLLoader } from "three/examples/jsm/loaders/STLLoader.js";
import { ThreeMFLoader } from "three/examples/jsm/loaders/3MFLoader.js";
import JSZip from "jszip";

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
  console.log("Завантаження 3MF, розмір:", blob.size);
  
  try {
    // ThreeMFLoader потребує URL, але може не працювати з blob URL
    // Спробуємо спочатку через ThreeMFLoader, якщо не вийде - розпакуємо ZIP вручну
    const zipUrl = URL.createObjectURL(blob);
    console.log("Створено URL для 3MF ZIP");
    
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
      console.log("3MF ZIP розпаковано, файлів:", Object.keys(zip.files).length);
      
      // Шукаємо .model файл
      const modelFile = zip.file("3D/3dmodel.model");
      if (!modelFile) {
        throw new Error("Не знайдено файл 3D/3dmodel.model в 3MF");
      }
      
      const modelBlob = await modelFile.async('blob');
      console.log("Файл моделі витягнуто, розмір:", modelBlob.size);
      
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
              console.log("STL геометрія завантажена:", geometry.attributes.position.count, "вершин");
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
  const { downloadUrl } = useGenerationStore();
  const cameraRef = useRef<THREE.PerspectiveCamera | null>(null);
  
  useEffect(() => {
    // Налаштовуємо камеру для кращого перегляду (навіть без моделі)
    const timer = setTimeout(() => {
      if (cameraRef.current) {
        // Позиція камери для перегляду моделі розміром ~200-300 одиниць
        const distance = 300;
        cameraRef.current.position.set(distance, distance, distance);
        cameraRef.current.lookAt(0, 0, 0);
        cameraRef.current.updateProjectionMatrix();
        console.log("Камера налаштована:", cameraRef.current.position);
      }
    }, 100);
    
    return () => clearTimeout(timer);
  }, [downloadUrl]);
  
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

function ModelLoader({ rotateMode }: { rotateMode: RotateMode }) {
  const { downloadUrl, activeTaskId, exportFormat, showAllZones, taskIds, taskStatuses } = useGenerationStore();
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

        console.log("=== ІНФОРМАЦІЯ ПРО ТЕСТОВУ МОДЕЛЬ ===");
        console.log("Розміри до обробки:", size.x, size.y, size.z);
        console.log("Максимальний розмір:", maxDim);

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

  // Batch preview: показуємо всі готові зони одночасно (розкладаємо на сітці)
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
        const models = (await Promise.all(idsToLoad.map((id) => loadZoneModel(id)))).filter(Boolean) as any[];
        if (!models.length) {
          setModel(null);
          setLoading(false);
          return;
        }

        // Уніфікований масштаб для всіх (щоб не було різних “розмірів”)
        const maxDimGlobal = Math.max(...models.map((m) => m.maxDim || 1));
        const globalScale = maxDimGlobal > 0 ? 200 / maxDimGlobal : 1;

        const group = new THREE.Group();
        for (const m of models) {
          m.obj.scale.setScalar(globalScale);
          m.obj.updateMatrixWorld(true);
          group.add(m.obj);
        }

        // layout
        const boxes = models.map((m) => new THREE.Box3().setFromObject(m.obj));
        const sizes = boxes.map((b) => b.getSize(new THREE.Vector3()));
        const maxW = Math.max(...sizes.map((s) => s.x));
        const maxD = Math.max(...sizes.map((s) => s.z));
        const spacingX = maxW + 40;
        const spacingZ = maxD + 40;
        const cols = 2;

        models.forEach((m, i) => {
          const col = i % cols;
          const row = Math.floor(i / cols);
          m.obj.position.x += col * spacingX;
          m.obj.position.z += row * spacingZ;
          m.obj.updateMatrixWorld(true);
        });

        // center whole group
        const groupBox = new THREE.Box3().setFromObject(group);
        const gCenter = groupBox.getCenter(new THREE.Vector3());
        const gMin = groupBox.min.clone();
        group.position.x -= gCenter.x;
        group.position.z -= gCenter.z;
        group.position.y -= gMin.y;
        group.updateMatrixWorld(true);

        (group as any).userData = { batch: true, ids: idsToLoad };
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
  const { downloadUrl, isGenerating, progress } = useGenerationStore();
  const [gridVisible, setGridVisible] = useState(true);
  const [axesVisible, setAxesVisible] = useState(true);
  const [rotateMode, setRotateMode] = useState<RotateMode>("camera");
  
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
          <div className="text-[10px] text-white/70">
            {rotateMode === "model" ? "Drag по моделі = rotate. Double-click = reset." : "Drag = rotate camera."}
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
          <OrbitControls 
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
