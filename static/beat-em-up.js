(function () {
  'use strict';

  var canvas = document.getElementById('gameCanvas');
  var ctx = canvas.getContext('2d');
  var VIEW_W = 960;
  var VIEW_H = 540;
  var WORLD_W = 3440;
  var LANE_MIN = 352;
  var LANE_MAX = 500;
  var dpr = 1;

  var startOverlay = document.getElementById('startOverlay');
  var pauseOverlay = document.getElementById('pauseOverlay');
  var endOverlay = document.getElementById('endOverlay');
  var pauseBtn = document.getElementById('pauseBtn');
  var waveToast = document.getElementById('waveToast');
  var toastTimer = 0;

  var keys = Object.create(null);
  var touchInput = { x: 0, y: 0 };
  var joystickPointer = null;
  var game = null;

  var TYPES = {
    player: {
      name: '阿岚', hp: 120, speed: 205, laneSpeed: 138, width: 38, height: 82,
      palette: { jacket: '#23c9b8', jacketDark: '#128b82', pants: '#172640', skin: '#efbd92',
        hair: '#101621', accent: '#ffcc67', shoe: '#e8edf7' }
    },
    thug: {
      name: '打手', hp: 46, speed: 96, laneSpeed: 72, width: 34, height: 74,
      attack: { duration: 0.54, activeStart: 0.25, activeEnd: 0.38, range: 47, lane: 27,
        damage: 8, knock: 105, cooldown: 1.15 },
      palette: { jacket: '#c54d48', jacketDark: '#7d2e32', pants: '#252b3b', skin: '#d9a279',
        hair: '#17131a', accent: '#ffb347', shoe: '#11151f' },
      score: 120
    },
    brute: {
      name: '壮汉', hp: 92, speed: 70, laneSpeed: 58, width: 48, height: 91,
      attack: { duration: 0.76, activeStart: 0.38, activeEnd: 0.54, range: 60, lane: 31,
        damage: 14, knock: 160, cooldown: 1.65 },
      palette: { jacket: '#7557a7', jacketDark: '#49356c', pants: '#23283a', skin: '#c88d66',
        hair: '#17131a', accent: '#b7a6ff', shoe: '#11151f' },
      score: 240
    },
    boss: {
      name: '铁塔', hp: 210, speed: 78, laneSpeed: 61, width: 58, height: 108,
      attack: { duration: 0.68, activeStart: 0.30, activeEnd: 0.47, range: 72, lane: 35,
        damage: 17, knock: 185, cooldown: 1.25 },
      palette: { jacket: '#b48a3d', jacketDark: '#68491d', pants: '#181e2c', skin: '#ba7f58',
        hair: '#090b11', accent: '#ff5e62', shoe: '#090c12' },
      score: 800
    }
  };

  var PLAYER_ATTACKS = {
    punch: { duration: 0.28, activeStart: 0.07, activeEnd: 0.17, range: 56, lane: 28,
      damage: 10, knock: 125, heavy: false },
    kick: { duration: 0.48, activeStart: 0.16, activeEnd: 0.30, range: 78, lane: 31,
      damage: 18, knock: 245, heavy: true },
    jumpKick: { duration: 0.42, activeStart: 0.06, activeEnd: 0.26, range: 72, lane: 35,
      damage: 21, knock: 260, heavy: true }
  };

  var WAVES = [
    {
      trigger: 520, gate: 1160, label: '街口 1 · 拦路',
      spawns: [
        { type: 'thug', x: 790, z: 405, side: 1 },
        { type: 'thug', x: 1010, z: 475, side: -1 }
      ]
    },
    {
      trigger: 1430, gate: 2280, label: '街口 2 · 围攻',
      spawns: [
        { type: 'brute', x: 1770, z: 430, side: 1 },
        { type: 'thug', x: 1960, z: 382, side: -1 },
        { type: 'thug', x: 2130, z: 488, side: 1 }
      ]
    },
    {
      trigger: 2500, gate: 3290, label: '尽头 · 铁塔',
      spawns: [
        { type: 'boss', x: 3030, z: 438, side: 1 },
        { type: 'thug', x: 2790, z: 386, side: -1 },
        { type: 'thug', x: 3160, z: 486, side: 1 }
      ]
    }
  ];

  var stars = [];
  var buildings = [];
  var streetProps = [];
  var audio = { context: null, enabled: true };

  function seeded(index) {
    var value = Math.sin(index * 917.231 + 41.73) * 43758.5453;
    return value - Math.floor(value);
  }

  function clamp(value, minimum, maximum) {
    return Math.max(minimum, Math.min(maximum, value));
  }

  function approach(value, target, step) {
    if (value < target) return Math.min(target, value + step);
    if (value > target) return Math.max(target, value - step);
    return target;
  }

  function removeDead(list) {
    for (var i = list.length - 1; i >= 0; i -= 1) {
      if (list[i].remove) list.splice(i, 1);
    }
  }

  function buildWorld() {
    stars = [];
    for (var i = 0; i < 95; i += 1) {
      stars.push({
        x: seeded(i + 1) * 1600,
        y: 22 + seeded(i + 51) * 230,
        size: seeded(i + 101) > 0.8 ? 2 : 1,
        alpha: 0.2 + seeded(i + 151) * 0.55
      });
    }
    buildings = [];
    var x = -120;
    var index = 0;
    while (x < WORLD_W + 400) {
      var width = 120 + seeded(index + 11) * 180;
      var height = 120 + seeded(index + 71) * 180;
      buildings.push({
        x: x,
        width: width,
        height: height,
        color: ['#111a2a', '#151f31', '#0d1725', '#182235'][index % 4],
        window: seeded(index + 91) > 0.42
      });
      x += width - 16;
      index += 1;
    }
    streetProps = [];
    for (var px = 250; px < WORLD_W; px += 470) {
      streetProps.push({ x: px, type: px % 2 === 0 ? 'lamp' : 'bin' });
    }
  }

  function newPlayer() {
    var stats = TYPES.player;
    return {
      type: 'player', name: stats.name, x: 180, z: 450, airY: 0, vy: 0,
      hp: stats.hp, maxHp: stats.hp, speed: stats.speed, laneSpeed: stats.laneSpeed,
      width: stats.width, height: stats.height, facing: 1,
      action: 'idle', actionTime: 0, attack: null, attackHit: false,
      cooldown: 0, invuln: 0, flash: 0, vx: 0,
      combo: 0, comboTimer: 0, palette: stats.palette, maxCombo: 0, separator: 0
    };
  }

  function newEnemy(spec, index) {
    var stats = TYPES[spec.type];
    return {
      type: spec.type, name: stats.name, x: spec.x, z: spec.z, airY: 0, vy: 0,
      hp: stats.hp, maxHp: stats.hp, speed: stats.speed, laneSpeed: stats.laneSpeed,
      width: stats.width, height: stats.height, facing: -1,
      action: 'idle', actionTime: 0, attack: stats.attack, attackHit: false,
      cooldown: 0.4 + seeded(index + game.waveIndex * 20) * 0.6,
      invuln: 0, flash: 0, vx: 0, dead: false, remove: false, deathTime: 0,
      side: spec.side || 1, laneOffset: spec.side > 0 ? 12 : -14,
      palette: stats.palette, score: stats.score, separator: index
    };
  }

  function createGame() {
    return {
      mode: 'intro', player: newPlayer(), enemies: [], particles: [], floaters: [],
      cameraX: 0, waveIndex: 0, waveActive: false, currentWave: null,
      score: 0, time: 0, shake: 0, flashRed: 0, hitFreeze: 0,
      toastUntil: 0, endTimer: 0, result: '', backgroundTime: 0
    };
  }

  function resetGame() {
    game = createGame();
    inputReset();
    pauseBtn.hidden = true;
    startOverlay.hidden = false;
    pauseOverlay.hidden = true;
    endOverlay.hidden = true;
    drawFrame();
  }

  function startGame() {
    audioStart();
    game = createGame();
    game.mode = 'playing';
    startOverlay.hidden = true;
    pauseOverlay.hidden = true;
    endOverlay.hidden = true;
    pauseBtn.hidden = false;
    inputReset();
    canvas.focus();
    showToast('先往右走，进入第一个街口');
    sound('start');
  }

  function togglePause(forcePause) {
    if (!game || (game.mode !== 'playing' && game.mode !== 'paused')) return;
    var pause = forcePause === true || game.mode === 'playing';
    if (pause) {
      if (game.mode !== 'playing') return;
      game.mode = 'paused';
      pauseOverlay.hidden = false;
      pauseBtn.textContent = '继续';
      inputReset();
    } else {
      if (game.mode !== 'paused') return;
      game.mode = 'playing';
      pauseOverlay.hidden = true;
      pauseBtn.textContent = '暂停';
      canvas.focus();
    }
  }

  function finishGame(won) {
    if (game.mode === 'ended') return;
    game.mode = 'ended';
    game.result = won ? 'win' : 'lose';
    pauseBtn.hidden = true;
    inputReset();
    document.getElementById('endEyebrow').textContent = won ? 'STAGE CLEAR' : 'KNOCKED DOWN';
    document.getElementById('endTitle').textContent = won ? '街区已清空' : '今晚先到这里';
    document.getElementById('endText').textContent = won
      ? '得分 ' + game.score + ' · 最高连击 ' + game.player.maxCombo + '。铁塔已经倒下，夜巷恢复安静。'
      : '得分 ' + game.score + '。调整距离，多用后撤和踢击，再试一次。';
    endOverlay.hidden = false;
    sound(won ? 'win' : 'lose');
  }

  function showToast(text) {
    waveToast.textContent = text;
    waveToast.classList.add('show');
    window.clearTimeout(toastTimer);
    toastTimer = window.setTimeout(function () {
      waveToast.classList.remove('show');
    }, 1800);
  }

  function inputReset() {
    for (var key in keys) keys[key] = false;
    touchInput.x = 0;
    touchInput.y = 0;
    setJoystickVisual(0, 0);
  }

  function inputAxis() {
    var x = touchInput.x;
    var y = touchInput.y;
    if (keys.a || keys.arrowleft) x -= 1;
    if (keys.d || keys.arrowright) x += 1;
    if (keys.w || keys.arrowup) y -= 1;
    if (keys.s || keys.arrowdown) y += 1;
    var length = Math.hypot(x, y);
    if (length > 1) {
      x /= length;
      y /= length;
    }
    return { x: x, y: y };
  }

  function nearestEnemy() {
    var player = game.player;
    var best = null;
    var bestDistance = Infinity;
    game.enemies.forEach(function (enemy) {
      if (enemy.dead) return;
      var distance = Math.hypot(enemy.x - player.x, enemy.z - player.z);
      if (distance < bestDistance) {
        best = enemy;
        bestDistance = distance;
      }
    });
    return best;
  }

  function startPlayerAttack(kind) {
    if (!game || game.mode !== 'playing') return;
    var player = game.player;
    if (player.hp <= 0 || player.action === 'hurt' || player.action === 'down') return;
    if (player.action === 'attack') return;
    if (player.cooldown > 0) return;
    if (player.airY > 0 && kind !== 'punch') return;
    var attackType = player.airY > 0 ? 'jumpKick' : kind;
    var attack = PLAYER_ATTACKS[attackType];
    var target = nearestEnemy();
    if (target && Math.abs(target.x - player.x) > 3) player.facing = target.x > player.x ? 1 : -1;
    player.action = 'attack';
    player.actionTime = 0;
    player.attack = attack;
    player.attackHit = false;
    player.cooldown = attack.duration * 0.82;
    sound('swing');
  }

  function startEnemyAttack(enemy) {
    enemy.action = 'attack';
    enemy.actionTime = 0;
    enemy.attackHit = false;
    enemy.facing = game.player.x >= enemy.x ? 1 : -1;
    sound('swing', 0.7);
  }

  function strikeLane(attacker, target, attack) {
    var dx = target.x - attacker.x;
    if (Math.abs(dx) > attack.range) return false;
    if (Math.abs(target.z - attacker.z) > attack.lane) return false;
    if (Math.abs(dx) > 6 && Math.sign(dx) !== attacker.facing) return false;
    return true;
  }

  function addBurst(x, y, z, color, heavy) {
    var count = heavy ? 10 : 6;
    for (var i = 0; i < count; i += 1) {
      game.particles.push({
        x: x, y: y, z: z,
        vx: (seeded(game.time * 900 + i + Math.random() * 100) - 0.5) * (heavy ? 220 : 150),
        vy: -40 - Math.random() * (heavy ? 150 : 90),
        life: 0.2 + Math.random() * 0.28, maxLife: 0.48,
        color: color || (heavy ? '#ffd166' : '#f4f7ff'),
        size: heavy ? 4 : 3
      });
    }
  }

  function addFloater(text, x, z, color) {
    game.floaters.push({ text: text, x: x, z: z, life: 0.85, color: color || '#fff' });
  }

  function hitEnemy(enemy, attack) {
    if (enemy.dead || enemy.invuln > 0 || enemy.action === 'down') return false;
    enemy.hp = Math.max(0, enemy.hp - attack.damage);
    enemy.flash = 0.11;
    enemy.invuln = 0.16;
    enemy.action = 'hurt';
    enemy.actionTime = 0;
    enemy.attackHit = true;
    enemy.vx = game.player.facing * attack.knock;
    game.player.combo += 1;
    game.player.comboTimer = 0.95;
    game.player.maxCombo = Math.max(game.player.maxCombo, game.player.combo);
    game.score += attack.damage * 3 + game.player.combo * 2;
    game.hitFreeze = attack.heavy ? 0.065 : 0.035;
    game.shake = Math.max(game.shake, attack.heavy ? 9 : 5);
    addBurst(enemy.x, enemy.z - enemy.height * 0.63, enemy.z, '#fff0a6', attack.heavy);
    addFloater('-' + attack.damage, enemy.x, enemy.z - enemy.height - 8,
      attack.heavy ? '#ffd166' : '#f7fbff');
    sound(attack.heavy ? 'heavy' : 'hit');
    if (enemy.hp <= 0) {
      enemy.dead = true;
      enemy.action = 'down';
      enemy.actionTime = 0;
      enemy.deathTime = 0;
      enemy.vx = game.player.facing * (attack.knock + 80);
      game.score += enemy.score;
      addFloater('+' + enemy.score, enemy.x, enemy.z - enemy.height - 26, '#52e0d0');
      sound(enemy.type === 'boss' ? 'bossDown' : 'ko');
      if (enemy.type === 'boss') {
        game.shake = 16;
        showToast('铁塔倒下了');
      }
    }
    return true;
  }

  function damagePlayer(enemy) {
    var player = game.player;
    if (player.invuln > 0 || player.hp <= 0 || player.action === 'down') return;
    var damage = enemy.attack.damage;
    player.hp = Math.max(0, player.hp - damage);
    player.flash = 0.16;
    player.invuln = 0.75;
    player.combo = 0;
    game.flashRed = 0.32;
    game.shake = 12;
    game.hitFreeze = 0.05;
    player.vx = enemy.facing * enemy.attack.knock;
    addBurst(player.x, player.z - player.height * 0.6, player.z, '#ff7386', true);
    addFloater('-' + damage, player.x, player.z - player.height - 4, '#ff8291');
    sound('hurt');
    if (player.hp <= 0) {
      player.action = 'down';
      player.actionTime = 0;
      game.endTimer = 1.35;
    } else {
      player.action = 'hurt';
      player.actionTime = 0;
    }
  }

  function updatePlayer(dt) {
    var player = game.player;
    player.cooldown = Math.max(0, player.cooldown - dt);
    player.invuln = Math.max(0, player.invuln - dt);
    player.flash = Math.max(0, player.flash - dt);
    player.comboTimer = Math.max(0, player.comboTimer - dt);
    if (player.comboTimer <= 0) player.combo = 0;

    if (player.airY > 0 || player.vy > 0) {
      player.vy -= 1050 * dt;
      player.airY += player.vy * dt;
      if (player.airY <= 0) {
        player.airY = 0;
        player.vy = 0;
        if (player.action === 'jump' || player.action === 'jumpKick') player.action = 'idle';
      }
    }

    if (player.action === 'hurt' || player.action === 'down') {
      player.actionTime += dt;
      player.x += player.vx * dt;
      player.vx *= Math.pow(0.025, dt);
      if (player.hp > 0 && player.action === 'hurt' && player.actionTime > 0.28) {
        player.action = 'idle';
      }
      clampPlayer();
      return;
    }

    if (player.action === 'attack') {
      player.actionTime += dt;
      var attack = player.attack;
      if (player.actionTime < 0.11) {
        player.x += player.facing * (attack.heavy ? 120 : 85) * dt;
      }
      if (!player.attackHit && player.actionTime >= attack.activeStart &&
          player.actionTime <= attack.activeEnd) {
        game.enemies.forEach(function (enemy) {
          if (!enemy.dead && strikeLane(player, enemy, attack)) {
            if (hitEnemy(enemy, attack)) player.attackHit = true;
          }
        });
      }
      if (player.actionTime >= attack.duration) {
        player.action = player.airY > 0 ? 'jump' : 'idle';
      }
      clampPlayer();
      return;
    }

    var axis = inputAxis();
    if (Math.abs(axis.x) > 0.08) player.facing = axis.x > 0 ? 1 : -1;
    player.x += axis.x * player.speed * dt;
    player.z += axis.y * player.laneSpeed * dt;
    player.action = (Math.abs(axis.x) + Math.abs(axis.y) > 0.08)
      ? (player.airY > 0 ? 'jump' : 'walk')
      : (player.airY > 0 ? 'jump' : 'idle');
    clampPlayer();
  }

  function clampPlayer() {
    var player = game.player;
    player.x = clamp(player.x, 45, WORLD_W - 70);
    player.z = clamp(player.z, LANE_MIN, LANE_MAX);
    if (game.waveActive && game.currentWave) {
      player.x = Math.min(player.x, game.currentWave.gate);
    }
  }

  function updateEnemy(enemy, dt) {
    enemy.flash = Math.max(0, enemy.flash - dt);
    enemy.invuln = Math.max(0, enemy.invuln - dt);
    enemy.cooldown = Math.max(0, enemy.cooldown - dt);
    enemy.actionTime += dt;

    if (enemy.dead) {
      enemy.deathTime += dt;
      enemy.x += enemy.vx * dt;
      enemy.vx *= Math.pow(0.018, dt);
      if (enemy.deathTime > 1.35) enemy.remove = true;
      return;
    }

    if (enemy.action === 'hurt') {
      enemy.x += enemy.vx * dt;
      enemy.vx *= Math.pow(0.02, dt);
      if (enemy.actionTime > 0.28) {
        enemy.action = 'idle';
        enemy.actionTime = 0;
        enemy.cooldown = Math.max(enemy.cooldown, 0.22);
      }
      return;
    }

    if (enemy.action === 'attack') {
      if (!enemy.attackHit && enemy.actionTime >= enemy.attack.activeStart &&
          enemy.actionTime <= enemy.attack.activeEnd) {
        if (strikeLane(enemy, game.player, enemy.attack)) {
          enemy.attackHit = true;
          damagePlayer(enemy);
        }
      }
      if (enemy.actionTime >= enemy.attack.duration) {
        enemy.action = 'idle';
        enemy.actionTime = 0;
        enemy.cooldown = enemy.attack.cooldown;
      }
      return;
    }

    var player = game.player;
    var dx = player.x - enemy.x;
    var dz = player.z - enemy.z;
    enemy.facing = dx >= 0 ? 1 : -1;
    var desiredX = player.x + enemy.side * (enemy.type === 'boss' ? 14 : 30);
    var desiredZ = player.z + enemy.laneOffset;
    var canStrike = Math.abs(dx) <= enemy.attack.range && Math.abs(dz) <= enemy.attack.lane;

    if (player.hp <= 0 || player.action === 'down') {
      enemy.x -= enemy.facing * enemy.speed * 0.22 * dt;
      return;
    }
    if (canStrike && enemy.cooldown <= 0) {
      startEnemyAttack(enemy);
      return;
    }

    var moveX = Math.abs(desiredX - enemy.x) > 5;
    var moveZ = Math.abs(desiredZ - enemy.z) > 4;
    if (moveX) enemy.x = approach(enemy.x, desiredX, enemy.speed * dt);
    if (moveZ) enemy.z = approach(enemy.z, desiredZ, enemy.laneSpeed * dt);
    enemy.action = (moveX || moveZ) ? 'walk' : 'idle';
    enemy.x = clamp(enemy.x, 25, WORLD_W - 30);
    enemy.z = clamp(enemy.z, LANE_MIN - 10, LANE_MAX + 5);

    game.enemies.forEach(function (other) {
      if (other === enemy || other.dead) return;
      if (Math.abs(other.x - enemy.x) < 26 && Math.abs(other.z - enemy.z) < 19) {
        enemy.z += (enemy.separator % 2 ? 1 : -1) * enemy.laneSpeed * 0.32 * dt;
        enemy.z = clamp(enemy.z, LANE_MIN, LANE_MAX);
      }
    });
  }

  function spawnWave(index) {
    var wave = WAVES[index];
    game.currentWave = wave;
    game.waveActive = true;
    wave.spawns.forEach(function (spec, enemyIndex) {
      game.enemies.push(newEnemy(spec, enemyIndex));
    });
    showToast(wave.label);
    sound(index === WAVES.length - 1 ? 'boss' : 'wave');
  }

  function updateWaves(dt) {
    if (game.endTimer > 0) {
      game.endTimer -= dt;
      if (game.endTimer <= 0) finishGame(game.player.hp > 0);
      return;
    }
    if (!game.waveActive && game.waveIndex < WAVES.length &&
        game.player.x >= WAVES[game.waveIndex].trigger) {
      spawnWave(game.waveIndex);
    }
    if (game.waveActive) {
      var living = game.enemies.filter(function (enemy) { return !enemy.dead; }).length;
      if (!living) {
        game.waveActive = false;
        game.currentWave = null;
        game.score += 100;
        game.waveIndex += 1;
        if (game.waveIndex >= WAVES.length) {
          showToast('最后一名敌人倒下');
          game.endTimer = 1.8;
          sound('clear');
        } else {
          showToast('街口清空 · 继续向右');
        }
      }
    }
  }

  function updateParticles(dt) {
    for (var i = game.particles.length - 1; i >= 0; i -= 1) {
      var p = game.particles[i];
      p.life -= dt;
      p.x += p.vx * dt;
      p.y += p.vy * dt;
      p.vy += 340 * dt;
      if (p.life <= 0) game.particles.splice(i, 1);
    }
    for (var j = game.floaters.length - 1; j >= 0; j -= 1) {
      var item = game.floaters[j];
      item.life -= dt;
      item.y -= 28 * dt;
      if (item.life <= 0) game.floaters.splice(j, 1);
    }
  }

  function update(dt) {
    if (!game || game.mode !== 'playing') return;
    game.time += dt;
    game.backgroundTime += dt;
    game.shake = Math.max(0, game.shake - dt * 45);
    game.flashRed = Math.max(0, game.flashRed - dt * 2.8);
    if (game.hitFreeze > 0) {
      game.hitFreeze -= dt;
      updateParticles(dt);
      return;
    }
    updatePlayer(dt);
    game.enemies.forEach(function (enemy) { updateEnemy(enemy, dt); });
    removeDead(game.enemies);
    updateWaves(dt);
    updateParticles(dt);

    var targetCamera = clamp(game.player.x - VIEW_W * 0.38, 0, WORLD_W - VIEW_W);
    game.cameraX += (targetCamera - game.cameraX) * Math.min(1, dt * 5.2);
  }

  function roundedRect(x, y, width, height, radius) {
    var r = Math.min(radius, width / 2, height / 2);
    ctx.beginPath();
    ctx.moveTo(x + r, y);
    ctx.lineTo(x + width - r, y);
    ctx.quadraticCurveTo(x + width, y, x + width, y + r);
    ctx.lineTo(x + width, y + height - r);
    ctx.quadraticCurveTo(x + width, y + height, x + width - r, y + height);
    ctx.lineTo(x + r, y + height);
    ctx.quadraticCurveTo(x, y + height, x, y + height - r);
    ctx.lineTo(x, y + r);
    ctx.quadraticCurveTo(x, y, x + r, y);
    ctx.closePath();
  }

  function drawBackground(time) {
    var sky = ctx.createLinearGradient(0, 0, 0, 500);
    sky.addColorStop(0, '#070d18');
    sky.addColorStop(0.52, '#15253a');
    sky.addColorStop(1, '#291c2d');
    ctx.fillStyle = sky;
    ctx.fillRect(0, 0, VIEW_W, VIEW_H);

    ctx.save();
    ctx.translate(-game.cameraX * 0.12, 0);
    stars.forEach(function (star) {
      ctx.globalAlpha = star.alpha * (0.8 + Math.sin(time * 0.7 + star.x) * 0.2);
      ctx.fillStyle = '#d8efff';
      ctx.fillRect(star.x % (VIEW_W + 300), star.y, star.size, star.size);
    });
    ctx.restore();
    ctx.globalAlpha = 1;

    var moonX = VIEW_W - 150 - game.cameraX * 0.025;
    ctx.fillStyle = 'rgba(217,236,255,.82)';
    ctx.beginPath();
    ctx.arc(moonX, 82, 32, 0, Math.PI * 2);
    ctx.fill();
    ctx.fillStyle = '#0b1626';
    ctx.beginPath();
    ctx.arc(moonX + 13, 70, 31, 0, Math.PI * 2);
    ctx.fill();

    ctx.save();
    ctx.translate(-game.cameraX * 0.28, 0);
    buildings.forEach(function (building) {
      var y = 276 - building.height;
      ctx.fillStyle = building.color;
      ctx.fillRect(building.x, y, building.width, building.height);
      ctx.fillStyle = 'rgba(255,255,255,.035)';
      ctx.fillRect(building.x, y, 8, building.height);
      if (building.window) {
        for (var wx = building.x + 18; wx < building.x + building.width - 12; wx += 28) {
          for (var wy = y + 25; wy < 260; wy += 39) {
            var lit = seeded(wx * 0.3 + wy) > 0.58;
            ctx.fillStyle = lit ? (seeded(wx + wy) > 0.75 ? '#ffc86b' : '#6abdd1') : '#0b1220';
            ctx.globalAlpha = lit ? 0.38 : 0.72;
            ctx.fillRect(wx, wy, 9, 13);
          }
        }
        ctx.globalAlpha = 1;
      }
    });
    ctx.restore();

    ctx.fillStyle = '#111927';
    ctx.fillRect(0, 274, VIEW_W, 50);
    ctx.fillStyle = '#1b2839';
    ctx.fillRect(0, 314, VIEW_W, 28);
    ctx.fillStyle = '#25202d';
    ctx.fillRect(0, 338, VIEW_W, VIEW_H - 338);
    var roadGradient = ctx.createLinearGradient(0, 340, 0, VIEW_H);
    roadGradient.addColorStop(0, 'rgba(70,71,91,.34)');
    roadGradient.addColorStop(1, 'rgba(9,11,19,.92)');
    ctx.fillStyle = roadGradient;
    ctx.fillRect(0, 338, VIEW_W, VIEW_H - 338);

    ctx.save();
    ctx.translate(-game.cameraX, 0);
    for (var lineX = 0; lineX < WORLD_W; lineX += 120) {
      ctx.fillStyle = 'rgba(255,205,116,.16)';
      ctx.fillRect(lineX, 454, 62, 4);
    }
    ctx.strokeStyle = 'rgba(119,145,166,.12)';
    ctx.lineWidth = 1;
    for (var lane = 370; lane <= 520; lane += 38) {
      ctx.beginPath();
      ctx.moveTo(0, lane);
      ctx.lineTo(WORLD_W, lane);
      ctx.stroke();
    }

    streetProps.forEach(function (prop) {
      if (prop.x < game.cameraX - 120 || prop.x > game.cameraX + VIEW_W + 120) return;
      if (prop.type === 'lamp') drawLamp(prop.x);
      else drawBin(prop.x);
    });

    var gateX = WORLD_W - 130;
    ctx.fillStyle = '#101725';
    ctx.fillRect(gateX, 206, 100, 176);
    ctx.strokeStyle = 'rgba(255,206,107,.45)';
    ctx.lineWidth = 3;
    ctx.strokeRect(gateX + 7, 215, 86, 158);
    ctx.fillStyle = 'rgba(255,89,108,.82)';
    ctx.font = '700 12px "Microsoft YaHei", sans-serif';
    ctx.textAlign = 'center';
    ctx.fillText('尽 头', gateX + 50, 248);
    ctx.restore();

    var vignette = ctx.createRadialGradient(VIEW_W / 2, VIEW_H / 2, 190, VIEW_W / 2, VIEW_H / 2, 620);
    vignette.addColorStop(0, 'rgba(0,0,0,0)');
    vignette.addColorStop(1, 'rgba(0,0,0,.48)');
    ctx.fillStyle = vignette;
    ctx.fillRect(0, 0, VIEW_W, VIEW_H);
  }

  function drawLamp(x) {
    ctx.fillStyle = '#293448';
    ctx.fillRect(x, 214, 7, 190);
    ctx.fillRect(x - 8, 206, 25, 8);
    ctx.fillStyle = '#ffe2a6';
    ctx.beginPath();
    ctx.arc(x + 13, 211, 8, 0, Math.PI * 2);
    ctx.fill();
    var glow = ctx.createRadialGradient(x + 13, 211, 2, x + 13, 211, 75);
    glow.addColorStop(0, 'rgba(255,224,154,.22)');
    glow.addColorStop(1, 'rgba(255,224,154,0)');
    ctx.fillStyle = glow;
    ctx.fillRect(x - 65, 136, 160, 160);
  }

  function drawBin(x) {
    ctx.fillStyle = '#263143';
    ctx.fillRect(x, 360, 25, 52);
    ctx.fillStyle = '#101724';
    ctx.fillRect(x - 3, 355, 31, 8);
    ctx.strokeStyle = '#43536d';
    ctx.strokeRect(x + 2, 366, 21, 43);
  }

  function drawShadow(entity) {
    var scale = 1 - Math.min(0.55, entity.airY / 220);
    ctx.save();
    ctx.globalAlpha = 0.34 * scale;
    ctx.fillStyle = '#000';
    ctx.beginPath();
    ctx.ellipse(entity.x - game.cameraX, entity.z + 2,
      entity.width * 0.68 * scale, 8 * scale, 0, 0, Math.PI * 2);
    ctx.fill();
    ctx.restore();
  }

  function fighterColor(entity, key) {
    return entity.flash > 0 ? '#ffffff' : entity.palette[key];
  }

  function drawFighter(entity) {
    drawShadow(entity);
    var sx = entity.x - game.cameraX;
    var by = entity.z - entity.airY;
    var h = entity.height;
    var w = entity.width;
    var walking = entity.action === 'walk' || entity.action === 'jump';
    var phase = Math.sin(game.time * 10 + entity.separator) * (walking ? 1 : 0);
    var bob = walking ? Math.abs(Math.sin(game.time * 10 + entity.separator)) * 2 : 0;
    var attack = entity.action === 'attack' ? entity.actionTime : 0;
    var punching = entity.action === 'attack' &&
      (entity.attack === PLAYER_ATTACKS.punch || entity.attack === PLAYER_ATTACKS.jumpKick ||
       (entity.type !== 'player' && entity.actionTime < entity.attack.duration * 0.75));
    var kicking = entity.action === 'attack' && entity.attack === PLAYER_ATTACKS.kick;
    var facing = entity.facing || 1;

    ctx.save();
    ctx.translate(sx, by);
    ctx.scale(facing, 1);
    if (entity.action === 'down') {
      var fall = clamp(entity.actionTime / 0.25, 0, 1);
      ctx.rotate(fall * Math.PI * 0.46);
      ctx.translate(0, fall * h * 0.18);
    }

    ctx.lineCap = 'round';
    ctx.lineJoin = 'round';
    ctx.strokeStyle = '#080b12';
    ctx.lineWidth = 3;

    var legSwing = phase * h * 0.075;
    ctx.fillStyle = fighterColor(entity, 'pants');
    ctx.fillRect(-w * 0.28 + legSwing, -h * 0.35 - bob, w * 0.22, h * 0.35);
    ctx.fillRect(w * 0.06 - legSwing, -h * 0.35 - bob, w * 0.22, h * 0.35);
    ctx.fillStyle = fighterColor(entity, 'shoe');
    ctx.fillRect(-w * 0.35 + legSwing, -5, w * 0.34, 6);
    ctx.fillRect(w * 0.02 - legSwing, -5, w * 0.34, 6);

    ctx.fillStyle = fighterColor(entity, 'jacketDark');
    roundedRect(-w * 0.44, -h * 0.76 - bob, w * 0.88, h * 0.44, 7);
    ctx.fill();
    ctx.fillStyle = fighterColor(entity, 'jacket');
    roundedRect(-w * 0.38, -h * 0.74 - bob, w * 0.76, h * 0.38, 6);
    ctx.fill();
    ctx.fillStyle = fighterColor(entity, 'accent');
    ctx.fillRect(-w * 0.31, -h * 0.66 - bob, w * 0.62, 4);

    var armY = -h * 0.64 - bob;
    ctx.strokeStyle = '#080b12';
    ctx.lineWidth = w * 0.29;
    if (punching) {
      ctx.beginPath();
      ctx.moveTo(w * 0.2, armY);
      ctx.lineTo(w * 0.78 + Math.sin(attack * 25) * 12, armY - 4);
      ctx.stroke();
      ctx.strokeStyle = fighterColor(entity, 'skin');
      ctx.lineWidth = w * 0.22;
      ctx.beginPath();
      ctx.moveTo(w * 0.2, armY);
      ctx.lineTo(w * 0.78 + Math.sin(attack * 25) * 12, armY - 4);
      ctx.stroke();
    } else {
      ctx.beginPath();
      ctx.moveTo(-w * 0.2, armY);
      ctx.lineTo(-w * 0.42 - phase * 3, armY + h * 0.25);
      ctx.moveTo(w * 0.2, armY);
      ctx.lineTo(w * 0.42 + phase * 3, armY + h * 0.24);
      ctx.stroke();
      ctx.strokeStyle = fighterColor(entity, 'skin');
      ctx.lineWidth = w * 0.2;
      ctx.beginPath();
      ctx.moveTo(-w * 0.2, armY);
      ctx.lineTo(-w * 0.42 - phase * 3, armY + h * 0.24);
      ctx.moveTo(w * 0.2, armY);
      ctx.lineTo(w * 0.42 + phase * 3, armY + h * 0.23);
      ctx.stroke();
    }

    if (kicking) {
      ctx.strokeStyle = '#080b12';
      ctx.lineWidth = w * 0.31;
      ctx.beginPath();
      ctx.moveTo(0, -h * 0.33);
      ctx.lineTo(w * 0.92, -h * 0.43);
      ctx.stroke();
      ctx.strokeStyle = fighterColor(entity, 'pants');
      ctx.lineWidth = w * 0.24;
      ctx.beginPath();
      ctx.moveTo(0, -h * 0.33);
      ctx.lineTo(w * 0.92, -h * 0.43);
      ctx.stroke();
      ctx.fillStyle = fighterColor(entity, 'shoe');
      ctx.fillRect(w * 0.87, -h * 0.47, w * 0.26, 10);
    }

    ctx.fillStyle = fighterColor(entity, 'skin');
    ctx.beginPath();
    ctx.arc(0, -h * 0.84 - bob, w * 0.24, 0, Math.PI * 2);
    ctx.fill();
    ctx.fillStyle = fighterColor(entity, 'hair');
    ctx.beginPath();
    ctx.arc(-w * 0.03, -h * 0.88 - bob, w * 0.25, Math.PI, Math.PI * 2.03);
    ctx.fill();
    ctx.fillRect(-w * 0.27, -h * 0.88 - bob, w * 0.12, 8);
    ctx.fillStyle = '#172033';
    ctx.fillRect(w * 0.09, -h * 0.85 - bob, 3, 3);

    ctx.restore();
    if (entity.type !== 'player' && !entity.dead) drawEnemyHealth(entity);
  }

  function drawEnemyHealth(enemy) {
    var sx = enemy.x - game.cameraX;
    var y = enemy.z - enemy.airY - enemy.height - 14;
    var width = enemy.type === 'boss' ? 78 : 54;
    ctx.fillStyle = 'rgba(3,5,10,.8)';
    ctx.fillRect(sx - width / 2, y, width, 6);
    ctx.fillStyle = enemy.type === 'boss' ? '#ffbf4d' : '#ff596f';
    ctx.fillRect(sx - width / 2 + 1, y + 1, (width - 2) * (enemy.hp / enemy.maxHp), 4);
    if (enemy.type === 'boss') {
      ctx.fillStyle = '#ffd88c';
      ctx.font = '700 9px "Microsoft YaHei", sans-serif';
      ctx.textAlign = 'center';
      ctx.fillText('铁塔', sx, y - 4);
    }
  }

  function drawEffects() {
    game.particles.forEach(function (p) {
      ctx.globalAlpha = clamp(p.life / p.maxLife, 0, 1);
      ctx.fillStyle = p.color;
      ctx.fillRect(p.x - game.cameraX, p.y, p.size, p.size);
    });
    ctx.globalAlpha = 1;
    game.floaters.forEach(function (item) {
      ctx.globalAlpha = clamp(item.life / 0.85, 0, 1);
      ctx.font = '800 14px "Microsoft YaHei", sans-serif';
      ctx.textAlign = 'center';
      ctx.fillStyle = item.color;
      ctx.fillText(item.text, item.x - game.cameraX, item.y);
    });
    ctx.globalAlpha = 1;
  }

  function drawHud() {
    var player = game.player;
    ctx.save();
    ctx.fillStyle = 'rgba(5,8,16,.75)';
    roundedRect(18, 16, 278, 64, 12);
    ctx.fill();
    ctx.fillStyle = '#f5f8ff';
    ctx.font = '800 13px "Microsoft YaHei", sans-serif';
    ctx.textAlign = 'left';
    ctx.fillText('阿岚', 32, 38);
    ctx.fillStyle = 'rgba(255,255,255,.12)';
    roundedRect(32, 48, 204, 14, 7);
    ctx.fill();
    var hpColor = player.hp / player.maxHp > 0.32 ? '#52e0d0' : '#ff596f';
    ctx.fillStyle = hpColor;
    roundedRect(34, 50, 200 * (player.hp / player.maxHp), 10, 5);
    ctx.fill();
    ctx.fillStyle = '#93a4bd';
    ctx.font = '700 10px "Microsoft YaHei", sans-serif';
    ctx.fillText(Math.ceil(player.hp) + ' / ' + player.maxHp, 244, 59);

    ctx.textAlign = 'center';
    ctx.fillStyle = 'rgba(5,8,16,.68)';
    roundedRect(VIEW_W / 2 - 82, 16, 164, 34, 17);
    ctx.fill();
    ctx.fillStyle = '#aebbd1';
    ctx.font = '700 10px "Microsoft YaHei", sans-serif';
    ctx.fillText(game.waveActive
      ? '街口 ' + Math.min(WAVES.length, game.waveIndex + 1) + ' / ' + WAVES.length
      : '寻找下一个街口', VIEW_W / 2, 38);

    ctx.textAlign = 'right';
    ctx.fillStyle = '#f5f8ff';
    ctx.font = '900 19px "Segoe UI", sans-serif';
    ctx.fillText(String(game.score).padStart(5, '0'), VIEW_W - 24, 38);
    ctx.fillStyle = '#73819a';
    ctx.font = '700 9px "Segoe UI", sans-serif';
    ctx.fillText('SCORE', VIEW_W - 24, 51);

    if (game.player.combo > 1) {
      ctx.textAlign = 'left';
      ctx.fillStyle = '#ffd166';
      ctx.font = '900 20px "Microsoft YaHei", sans-serif';
      ctx.fillText(game.player.combo + ' COMBO', 32, 112);
    }
    if (game.flashRed > 0) {
      ctx.fillStyle = 'rgba(255,40,70,' + (game.flashRed * 0.28) + ')';
      ctx.fillRect(0, 0, VIEW_W, VIEW_H);
    }
    ctx.restore();
  }

  function drawFrame() {
    if (!game) return;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, VIEW_W, VIEW_H);
    ctx.save();
    if (game.shake > 0) {
      ctx.translate((Math.random() - 0.5) * game.shake, (Math.random() - 0.5) * game.shake);
    }
    drawBackground(game.backgroundTime);
    var drawables = [game.player].concat(game.enemies).sort(function (a, b) {
      return a.z - b.z;
    });
    drawables.forEach(drawFighter);
    drawEffects();
    ctx.restore();
    drawHud();
    if (!game.waveActive && game.mode === 'playing' && game.waveIndex > 0 &&
        game.waveIndex < WAVES.length) {
      ctx.save();
      ctx.globalAlpha = 0.55 + Math.sin(game.time * 4) * 0.18;
      ctx.fillStyle = '#52e0d0';
      ctx.font = '800 12px "Microsoft YaHei", sans-serif';
      ctx.textAlign = 'center';
      ctx.fillText('向右推进  →', VIEW_W * 0.72, 102);
      ctx.restore();
    }
  }

  function sound(name, scale) {
    if (!audio.context || !audio.enabled) return;
    var context = audio.context;
    var now = context.currentTime;
    var oscillator = context.createOscillator();
    var gain = context.createGain();
    oscillator.connect(gain);
    gain.connect(context.destination);
    var volume = scale || 1;

    if (name === 'hit' || name === 'heavy' || name === 'hurt') {
      oscillator.type = name === 'hurt' ? 'sawtooth' : 'square';
      oscillator.frequency.setValueAtTime(name === 'hit' ? 105 : 155, now);
      oscillator.frequency.exponentialRampToValueAtTime(name === 'hurt' ? 46 : 52, now + 0.10);
      gain.gain.setValueAtTime(0.0001, now);
      gain.gain.exponentialRampToValueAtTime(0.055 * volume, now + 0.008);
      gain.gain.exponentialRampToValueAtTime(0.0001, now + 0.12);
      oscillator.start(now);
      oscillator.stop(now + 0.13);
    } else if (name === 'swing') {
      oscillator.type = 'triangle';
      oscillator.frequency.setValueAtTime(180, now);
      oscillator.frequency.exponentialRampToValueAtTime(90, now + 0.08);
      gain.gain.setValueAtTime(0.0001, now);
      gain.gain.exponentialRampToValueAtTime(0.018 * volume, now + 0.01);
      gain.gain.exponentialRampToValueAtTime(0.0001, now + 0.09);
      oscillator.start(now);
      oscillator.stop(now + 0.1);
    } else if (name === 'bossDown' || name === 'ko') {
      oscillator.type = 'sawtooth';
      oscillator.frequency.setValueAtTime(name === 'bossDown' ? 135 : 115, now);
      oscillator.frequency.exponentialRampToValueAtTime(38, now + 0.38);
      gain.gain.setValueAtTime(0.0001, now);
      gain.gain.exponentialRampToValueAtTime(0.08 * volume, now + 0.015);
      gain.gain.exponentialRampToValueAtTime(0.0001, now + 0.42);
      oscillator.start(now);
      oscillator.stop(now + 0.44);
    } else {
      oscillator.type = 'sine';
      oscillator.frequency.setValueAtTime(
        name === 'wave' ? 420 : name === 'boss' ? 90 : name === 'clear' ? 520 : 320, now
      );
      oscillator.frequency.exponentialRampToValueAtTime(
        name === 'wave' ? 620 : name === 'boss' ? 60 : name === 'clear' ? 780 : 430, now + 0.22
      );
      gain.gain.setValueAtTime(0.0001, now);
      gain.gain.exponentialRampToValueAtTime(0.028 * volume, now + 0.02);
      gain.gain.exponentialRampToValueAtTime(0.0001, now + 0.28);
      oscillator.start(now);
      oscillator.stop(now + 0.3);
    }
  }

  function audioStart() {
    if (audio.context) {
      if (audio.context.state === 'suspended') audio.context.resume().catch(function () {});
      return;
    }
    var AudioCtor = window.AudioContext || window.webkitAudioContext;
    if (!AudioCtor) return;
    audio.context = new AudioCtor();
    audio.context.resume().catch(function () {});
  }

  function resizeCanvas() {
    dpr = Math.min(window.devicePixelRatio || 1, 2);
    canvas.width = Math.round(VIEW_W * dpr);
    canvas.height = Math.round(VIEW_H * dpr);
    drawFrame();
  }

  function setJoystickVisual(x, y) {
    var knob = document.getElementById('joystickKnob');
    knob.style.transform = 'translate(calc(-50% + ' + (x * 28) + 'px), calc(-50% + ' +
      (y * 28) + 'px))';
  }

  function updateJoystick(event) {
    var base = document.getElementById('joystick');
    var rect = base.getBoundingClientRect();
    var x = (event.clientX - rect.left - rect.width / 2) / (rect.width * 0.33);
    var y = (event.clientY - rect.top - rect.height / 2) / (rect.height * 0.33);
    var length = Math.hypot(x, y);
    if (length > 1) {
      x /= length;
      y /= length;
    }
    touchInput.x = x;
    touchInput.y = y;
    setJoystickVisual(x, y);
  }

  function bindControls() {
    document.addEventListener('keydown', function (event) {
      var key = event.key.toLowerCase();
      if (['arrowup', 'arrowdown', 'arrowleft', 'arrowright', ' '].indexOf(key) >= 0) {
        event.preventDefault();
      }
      if (game.mode === 'intro' && (key === 'enter' || key === ' ')) {
        startGame();
        return;
      }
      if (key === 'p') {
        togglePause();
        return;
      }
      keys[key] = true;
      if (event.repeat) return;
      if (key === 'j') startPlayerAttack('punch');
      else if (key === 'k') startPlayerAttack('kick');
      else if ((key === 'l' || key === ' ') && game.mode === 'playing') {
        if (game.player.airY === 0 && game.player.action !== 'hurt' &&
            game.player.action !== 'down') {
          game.player.vy = 335;
          game.player.airY = 0.1;
          game.player.action = 'jump';
          sound('swing');
        }
      }
    });
    document.addEventListener('keyup', function (event) {
      keys[event.key.toLowerCase()] = false;
    });
    document.getElementById('startBtn').addEventListener('click', startGame);
    document.getElementById('restartBtn').addEventListener('click', startGame);
    document.getElementById('resumeBtn').addEventListener('click', function () { togglePause(false); });
    pauseBtn.addEventListener('click', function () {
      togglePause(game.mode === 'playing');
    });
    document.querySelectorAll('[data-action]').forEach(function (button) {
      button.addEventListener('pointerdown', function (event) {
        event.preventDefault();
        var action = button.dataset.action;
        if (action === 'jump') {
          if (game.mode === 'playing' && game.player.airY === 0 &&
              game.player.action !== 'hurt' && game.player.action !== 'down') {
            game.player.vy = 335;
            game.player.airY = 0.1;
            game.player.action = 'jump';
            sound('swing');
          }
        } else {
          startPlayerAttack(action);
        }
      });
    });
    var joystick = document.getElementById('joystick');
    joystick.addEventListener('pointerdown', function (event) {
      event.preventDefault();
      joystickPointer = event.pointerId;
      joystick.setPointerCapture(event.pointerId);
      updateJoystick(event);
    });
    joystick.addEventListener('pointermove', function (event) {
      if (event.pointerId === joystickPointer) updateJoystick(event);
    });
    function endJoystick(event) {
      if (event.pointerId !== joystickPointer) return;
      joystickPointer = null;
      touchInput.x = 0;
      touchInput.y = 0;
      setJoystickVisual(0, 0);
    }
    joystick.addEventListener('pointerup', endJoystick);
    joystick.addEventListener('pointercancel', endJoystick);
    document.addEventListener('visibilitychange', function () {
      if (document.hidden) togglePause(true);
    });
    new ResizeObserver(resizeCanvas).observe(document.getElementById('gameShell'));
  }

  function snapshot() {
    return {
      mode: game.mode,
      score: game.score,
      hp: game.player.hp,
      combo: game.player.combo,
      maxCombo: game.player.maxCombo,
      waveIndex: game.waveIndex,
      waveActive: game.waveActive,
      enemies: game.enemies.filter(function (enemy) { return !enemy.dead; }).length,
      playerX: Math.round(game.player.x),
      playerZ: Math.round(game.player.z)
    };
  }

  function defeatLivingEnemies() {
    game.enemies.forEach(function (enemy) {
      if (enemy.dead) return;
      enemy.hp = 0;
      enemy.dead = true;
      enemy.action = 'down';
      enemy.actionTime = 0;
      enemy.deathTime = 0;
      enemy.vx = 150;
      game.score += enemy.score;
    });
  }

  function putEnemyAhead() {
    var enemy = game.enemies.find(function (item) { return !item.dead; });
    if (!enemy) return false;
    game.player.x = enemy.x - 38;
    game.player.z = enemy.z;
    game.player.facing = 1;
    game.player.action = 'idle';
    game.player.airY = 0;
    enemy.action = 'idle';
    enemy.cooldown = 2;
    return true;
  }

  var lastTime = performance.now();
  function loop(now) {
    var dt = Math.min(0.033, (now - lastTime) / 1000 || 0);
    lastTime = now;
    update(dt);
    drawFrame();
    requestAnimationFrame(loop);
  }

  function init() {
    buildWorld();
    game = createGame();
    bindControls();
    resizeCanvas();
    requestAnimationFrame(loop);
    window.TBNightAlley = {
      start: startGame,
      pause: togglePause,
      snapshot: snapshot,
      debug: {
        teleport: function (x, z) {
          game.player.x = clamp(Number(x) || 0, 45, WORLD_W - 70);
          game.player.z = clamp(Number(z) || 450, LANE_MIN, LANE_MAX);
        },
        putEnemyAhead: putEnemyAhead,
        defeatEnemies: defeatLivingEnemies,
        giveHealth: function () { game.player.hp = game.player.maxHp; }
      }
    };
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
