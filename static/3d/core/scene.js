// Three.js 场景：相机、灯光、网格、模型显示。只关心怎么把几何体画出来。

import * as THREE from '../vendor/three.module.js';
import { OrbitControls } from '../vendor/addons/controls/OrbitControls.js';

export function createViewer(host) {
  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(36, 1, 0.1, 10000);
  camera.position.set(220, 170, 250);

  const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = THREE.PCFSoftShadowMap;
  host.appendChild(renderer.domElement);

  const controls = new OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true;
  controls.dampingFactor = 0.075;
  controls.minDistance = 30;
  controls.maxDistance = 1800;
  controls.maxPolarAngle = Math.PI * 0.92;
  controls.target.set(0, 35, 0);

  const hemi = new THREE.HemisphereLight(0xffffff, 0x55716e, 2.2);
  scene.add(hemi);
  const keyLight = new THREE.DirectionalLight(0xffffff, 2.8);
  keyLight.position.set(180, 300, 220);
  keyLight.castShadow = true;
  keyLight.shadow.mapSize.set(1024, 1024);
  keyLight.shadow.camera.left = -300;
  keyLight.shadow.camera.right = 300;
  keyLight.shadow.camera.top = 300;
  keyLight.shadow.camera.bottom = -300;
  scene.add(keyLight);
  const fill = new THREE.DirectionalLight(0x9be9df, 1.0);
  fill.position.set(-220, 120, -160);
  scene.add(fill);

  let grid = null;
  let modelGroup = null;
  let currentBounds = null;

  function isDark() {
    return document.documentElement.getAttribute('data-theme') === 'dark';
  }

  function disposeModel() {
    if (!modelGroup) return;
    modelGroup.traverse((child) => {
      if (child.geometry) child.geometry.dispose();
      if (child.material) {
        if (Array.isArray(child.material)) child.material.forEach((item) => item.dispose());
        else child.material.dispose();
      }
    });
    scene.remove(modelGroup);
    modelGroup = null;
    currentBounds = null;
  }

  function setGeometry(geometry, color) {
    disposeModel();
    if (!geometry.boundingBox) geometry.computeBoundingBox();

    const material = new THREE.MeshStandardMaterial({
      color,
      roughness: 0.42,
      metalness: 0.04,
      side: THREE.DoubleSide
    });
    const mesh = new THREE.Mesh(geometry, material);
    mesh.castShadow = true;
    mesh.receiveShadow = true;

    const edgeMaterial = new THREE.LineBasicMaterial({
      color: isDark() ? 0xd8fff9 : 0x1f4b47,
      transparent: true,
      opacity: 0.19
    });
    const edges = new THREE.LineSegments(new THREE.EdgesGeometry(geometry, 28), edgeMaterial);

    modelGroup = new THREE.Group();
    modelGroup.add(mesh, edges);
    scene.add(modelGroup);
    currentBounds = geometry.boundingBox.clone();

    const size = new THREE.Vector3();
    currentBounds.getSize(size);
    return {
      bounds: currentBounds.clone(),
      stats: {
        width: size.x,
        height: size.y,
        depth: size.z,
        triangles: Math.floor(geometry.attributes.position.count / 3)
      }
    };
  }

  function clearModel() {
    disposeModel();
  }

  function setColor(color) {
    if (!modelGroup) return;
    const mesh = modelGroup.children.find((child) => child.isMesh);
    if (mesh) mesh.material.color.set(color);
  }

  function updateGrid(bounds) {
    if (grid) {
      scene.remove(grid);
      grid.geometry.dispose();
      grid.material.dispose();
      grid = null;
    }
    const span = Math.max(
      bounds.max.x - bounds.min.x,
      bounds.max.z - bounds.min.z,
      80
    ) * 2.2;
    grid = new THREE.GridHelper(span, 20, 0x6f918e, 0xaec5c2);
    grid.position.y = Math.min(0, bounds.min.y) - 0.15;
    grid.material.transparent = true;
    grid.material.opacity = isDark() ? 0.28 : 0.34;
    scene.add(grid);
  }

  function fitView(view = 'iso') {
    if (!modelGroup || !currentBounds) return;
    const center = currentBounds.getCenter(new THREE.Vector3());
    const size = currentBounds.getSize(new THREE.Vector3());
    const distance = Math.max(size.x, size.y, size.z, 60) * 2.25;
    controls.target.copy(center);
    camera.up.set(0, 1, 0);

    if (view === 'front') {
      camera.position.set(center.x, center.y, center.z + distance);
    } else if (view === 'top') {
      camera.up.set(0, 0, -1);
      camera.position.set(center.x, center.y + distance, center.z + 0.01);
    } else {
      camera.position.set(
        center.x + distance * 0.7,
        center.y + distance * 0.55,
        center.z + distance * 0.8
      );
    }
    camera.near = Math.max(0.1, distance / 800);
    camera.far = distance * 20;
    camera.updateProjectionMatrix();
    controls.update();
  }

  function refreshTheme() {
    if (modelGroup) {
      const edges = modelGroup.children.find((child) => child.isLineSegments);
      if (edges) edges.material.color.set(isDark() ? 0xd8fff9 : 0x1f4b47);
    }
    if (grid) grid.material.opacity = isDark() ? 0.28 : 0.34;
  }

  function resize() {
    const width = host.clientWidth;
    const height = host.clientHeight;
    if (!width || !height) return;
    renderer.setSize(width, height, false);
    camera.aspect = width / height;
    camera.updateProjectionMatrix();
  }

  const resizeObserver = new ResizeObserver(resize);
  resizeObserver.observe(host);
  resize();

  renderer.setAnimationLoop(() => {
    controls.update();
    renderer.render(scene, camera);
  });

  function dispose() {
    renderer.setAnimationLoop(null);
    resizeObserver.disconnect();
    disposeModel();
    if (grid) {
      grid.geometry.dispose();
      grid.material.dispose();
    }
    controls.dispose();
    renderer.dispose();
  }

  return {
    scene,
    camera,
    renderer,
    controls,
    setGeometry,
    clearModel,
    setColor,
    updateGrid,
    fitView,
    refreshTheme,
    resize,
    dispose
  };
}
