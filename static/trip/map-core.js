/* 高德 JS API 的懒加载与常用小工具
   ------------------------------------------------------------
   地图 Key 和安全密钥由 /trip/api/map/status 下发，本来就属于浏览器可见信息；
   Web 服务 Key 永远留在服务端。这里只加载一次脚本，多个页面共用。 */
(function () {
  'use strict';

  var promise = null;

  function load(status) {
    if (window.AMap) { return Promise.resolve(window.AMap); }
    if (promise) { return promise; }
    status = status || {};
    if (!status.map_ready || !status.js_key || !status.security_code) {
      return Promise.reject(new Error('没有配置地图 Key'));
    }
    promise = new Promise(function (resolve, reject) {
      window._AMapSecurityConfig = { securityJsCode: status.security_code };
      var script = document.createElement('script');
      script.async = true;
      script.src = 'https://webapi.amap.com/maps?v=2.0&key=' +
        encodeURIComponent(status.js_key) + '&plugin=AMap.ToolBar';
      script.onload = function () {
        if (window.AMap) { resolve(window.AMap); }
        else { reject(new Error('地图脚本没有初始化成功')); }
      };
      script.onerror = function () { reject(new Error('地图脚本加载失败')); };
      document.head.appendChild(script);
      setTimeout(function () {
        if (!window.AMap) { reject(new Error('地图加载超时')); }
      }, 12000);
    });
    return promise;
  }

  function pin(lng, lat, text, cssClass, title) {
    var content = document.createElement('div');
    content.className = 'amap-pin ' + (cssClass || '');
    content.textContent = text || '';
    return new window.AMap.Marker({
      position: [Number(lng), Number(lat)],
      content: content,
      offset: new window.AMap.Pixel(-13, -13),
      title: title || ''
    });
  }

  function clear(map, markers) {
    if (map && markers && markers.length) {
      try { map.remove(markers); } catch (err) { /* 地图已销毁时忽略 */ }
    }
  }

  function fit(map, markers, avoid) {
    if (map && markers && markers.length) {
      try { map.setFitView(markers, false, avoid || [40, 40, 40, 40]); } catch (err) { /* 忽略 */ }
    }
  }

  function infoWindow(html) {
    return new window.AMap.InfoWindow({ content: html, offset: new window.AMap.Pixel(0, -28) });
  }

  window.TBMapCore = {
    load: load,
    pin: pin,
    clear: clear,
    fit: fit,
    infoWindow: infoWindow
  };
})();
