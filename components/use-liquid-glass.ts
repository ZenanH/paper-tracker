'use client';
/*!
 * Refraction field ported from liquid-glass-js
 * (https://github.com/dashersw/liquid-glass-js) — Copyright (c) 2025 Armagan
 * Amcalar, MIT. Permission is hereby granted, free of charge, to any person
 * obtaining a copy of that software to deal in it without restriction,
 * provided this notice travels with it; it is provided "as is", without
 * warranty of any kind. Keep this header on any copy or port.
 */
import { useEffect, useRef } from 'react';

// One stable toolbar owns one filter. No animation loop or content observers.
export function useLiquidGlass() {
  const ref = useRef<HTMLElement>(null);
  useEffect(() => {
    const element = ref.current;
    const brands = (navigator as Navigator & { userAgentData?: { brands: { brand: string }[] } }).userAgentData?.brands;
    if (!element || !(brands?.some(b => /Chromium|Chrome|Edge/.test(b.brand)) || /Chrome\//.test(navigator.userAgent))) return;
    const ns = 'http://www.w3.org/2000/svg';
    const svg = document.createElementNS(ns, 'svg');
    svg.setAttribute('width', '0'); svg.setAttribute('height', '0'); svg.setAttribute('aria-hidden', 'true');
    svg.style.position = 'absolute'; svg.style.pointerEvents = 'none';
    document.body.append(svg);
    const id = `toolbar-glass-${crypto.randomUUID()}`;
    let timer: ReturnType<typeof setTimeout>, key = '';
    const rebuild = () => {
      const { width: w, height: h } = element.getBoundingClientRect();
      if (!w || !h || !element.isConnected) return;
      const radiusText = getComputedStyle(element).borderTopLeftRadius;
      const r = Math.min(Math.min(w, h) / 2, parseFloat(radiusText) * (radiusText.includes('%') ? Math.min(w, h) / 100 : 1));
      const pageW = innerWidth, pageH = innerHeight, nextKey = [w, h, r, pageW, pageH].join(':');
      if (key === nextKey && svg.firstChild?.isConnected) return;
      const factor = Math.max(.25, Math.min(2, 1400 / Math.max(w, h)));
      const canvas = document.createElement('canvas');
      canvas.width = Math.ceil(w * factor); canvas.height = Math.ceil(h * factor);
      const context = canvas.getContext('2d'); if (!context) return;
      const offsets = new Float32Array(canvas.width * canvas.height * 2);
      let maxAbs = 0;
      for (let y = 0; y < canvas.height; y++) for (let x = 0; x < canvas.width; x++) {
        const px = (x + .5) / canvas.width * w, py = (y + .5) / canvas.height * h;
        const cx = px / w, cy = py / h, tx = Math.abs(px - w / 2) - (w / 2 - r), ty = Math.abs(py - h / 2) - (h / 2 - r);
        const distance = Math.max(-(Math.hypot(Math.max(tx, 0), Math.max(ty, 0)) + Math.min(Math.max(tx, ty), 0) - r), 0);
        const rim = Math.exp(-distance * 1.7), total = Math.exp(-distance * .5) * .015 + rim * .028;
        const length = Math.hypot(cx - .5, cy - .5) || 1, nx = (cx - .5) / length, ny = (cy - .5) / length;
        const corner = Math.exp(-Math.max(Math.min(cx, 1 - cx), Math.min(cy, 1 - cy)) * Math.min(w, h) * .3) * .06;
        const ripple = Math.sin(distance / Math.min(w, h) * 25) * .26 * rim;
        const dx = (nx * (total + corner) - ny * ripple) * pageW, dy = (ny * (total + corner) + nx * ripple) * pageH;
        const index = (y * canvas.width + x) * 2;
        offsets[index] = dx; offsets[index + 1] = dy; maxAbs = Math.max(maxAbs, Math.abs(dx), Math.abs(dy));
      }
      const scale = Math.max(maxAbs * 2, .0001), bias = scale * (128 / 255 - .5), pixels = context.createImageData(canvas.width, canvas.height);
      for (let i = 0; i < offsets.length / 2; i++) {
        pixels.data[i * 4] = Math.round(255 * (.5 + (offsets[i * 2] - bias) / scale));
        pixels.data[i * 4 + 1] = Math.round(255 * (.5 + (offsets[i * 2 + 1] - bias) / scale));
        pixels.data[i * 4 + 2] = 128; pixels.data[i * 4 + 3] = 255;
      }
      context.putImageData(pixels, 0, 0);
      const filter = document.createElementNS(ns, 'filter'), margin = scale / 2 + 2.1;
      const attrs = (node: Element, values: Record<string, string | number>) => Object.entries(values).forEach(([name, value]) => node.setAttribute(name, String(value)));
      attrs(filter, { id, filterUnits: 'userSpaceOnUse', x: -margin, y: -margin, width: w + margin * 2, height: h + margin * 2, 'color-interpolation-filters': 'sRGB' });
      const map = document.createElementNS(ns, 'feImage'); attrs(map, { href: canvas.toDataURL(), x: 0, y: 0, width: w, height: h, preserveAspectRatio: 'none', result: 'map' });
      const displacement = document.createElementNS(ns, 'feDisplacementMap'); attrs(displacement, { in: 'SourceGraphic', in2: 'map', scale, xChannelSelector: 'R', yChannelSelector: 'G' });
      const blur = document.createElementNS(ns, 'feGaussianBlur'); attrs(blur, { stdDeviation: .7 });
      filter.append(map, displacement, blur); svg.replaceChildren(filter);
      element.style.backdropFilter = `url(#${id})`; key = nextKey;
    };
    const schedule = () => { clearTimeout(timer); timer = setTimeout(rebuild, 180); };
    const observer = new ResizeObserver(schedule); observer.observe(element);
    window.addEventListener('resize', schedule); schedule();
    return () => { clearTimeout(timer); observer.disconnect(); window.removeEventListener('resize', schedule); element.style.removeProperty('backdrop-filter'); svg.remove(); };
  }, []);
  return ref;
}
