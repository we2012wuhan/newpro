# 署名与改动说明

本目录是第三方静态作品「千人小镇 · Thousand Town」的接入副本。

- 原作者：仪绘士 / YiHuiShiBY
- 原项目：<https://github.com/YiHuiShiBY/thousand-town>
- 原作许可证：CC BY 4.0（完整文本见本目录 `LICENSE`）
- 第三方组件：Three.js r180 / Reflector / GLTFLoader（MIT）、p5.js 1.7.0（LGPL-2.1）、银河全景图（ESO / S. Brunier，CC BY 4.0）；完整许可见 `第三方许可/`

## 本工具箱的改动

- 将原作静态文件放入 `third_party/thousand-town/`，由 `main.py` 以 `/thousand-town/` 挂载，并放在 `/static` 之外，让登录门禁继续生效。
- 入口页把「返回作品集」替换为「返回工具箱」，并补充工具箱入口与署名链接。
- 平面图集、无人机图库、居民三维档案馆补充「返回工具箱」和「原作 · CC BY 4.0」链接。
- 默认关闭原作对外部留言板接口的请求，保持纯本地运行；如需联调，可在浏览器 `localStorage` 显式设置 `town_board_api` 后再开启。
- 未修改居民模拟、城市生成、三维渲染等原作逻辑。

建议引用格式：

> 基于「千人小镇 · Thousand Town」（仪绘士 / YiHuiShiBY，<https://github.com/YiHuiShiBY/thousand-town>，CC BY 4.0）修改，改动内容：接入本工具箱、替换返回链接并补充署名入口。
