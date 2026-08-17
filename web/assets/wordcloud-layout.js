/* Pure layout helper for the Garaye word cloud.
 * It has no DOM dependency so its central-placement and collision rules can
 * be checked in a small automated test as well as in the browser. */
(function attachGarayeWordCloudLayout(global) {
  "use strict";

  const GOLDEN_ANGLE = Math.PI * (3 - Math.sqrt(5));
  const MIN_FONT_SIZE = 14;
  const MAX_FONT_SIZE = 44;
  const PADDING = 8;

  function clamp(value, minimum, maximum) {
    return Math.max(minimum, Math.min(maximum, value));
  }

  function fontSize(count, minimumCount, maximumCount) {
    if (maximumCount <= minimumCount) return 28;
    return Math.round(
      MIN_FONT_SIZE + ((count - minimumCount) / (maximumCount - minimumCount)) * (MAX_FONT_SIZE - MIN_FONT_SIZE),
    );
  }

  function estimatedBox(word, size) {
    const letters = Array.from(String(word || "").replace(/\u200c/g, "")).length;
    return {
      width: Math.max(size * 1.85, Math.ceil(letters * size * 0.83) + 18),
      height: Math.ceil(size * 1.48) + 10,
    };
  }

  function intersects(candidate, placed) {
    return placed.some((item) => (
      Math.abs(candidate.x - item.x) < (candidate.width + item.width) / 2 + PADDING
      && Math.abs(candidate.y - item.y) < (candidate.height + item.height) / 2 + PADDING
    ));
  }

  function inside(candidate, width, height) {
    return (
      candidate.x - candidate.width / 2 >= PADDING
      && candidate.x + candidate.width / 2 <= width - PADDING
      && candidate.y - candidate.height / 2 >= PADDING
      && candidate.y + candidate.height / 2 <= height - PADDING
    );
  }

  function layout(words, viewportWidth, viewportHeight) {
    const width = Math.max(260, Number(viewportWidth) || 260);
    const height = Math.max(220, Number(viewportHeight) || 220);
    const input = Array.isArray(words) ? words.filter((item) => item && item.word) : [];
    if (!input.length) return [];

    const counts = input.map((item) => Math.max(0, Number(item.count) || 0));
    const maximum = Math.max(...counts);
    const minimum = Math.min(...counts);
    const centerX = width / 2;
    const centerY = height / 2;
    const placed = [];

    input.forEach((item, index) => {
      const size = fontSize(Math.max(0, Number(item.count) || 0), minimum, maximum);
      const box = estimatedBox(item.word, size);
      if (index === 0) {
        placed.push({
          ...item, ...box, x: centerX, y: centerY, fontSize: size, central: true,
        });
        return;
      }

      const central = placed[0];
      let radius = Math.max(central.width / 2 + box.width / 2 + PADDING * 2, 42) + Math.sqrt(index) * 8;
      let angle = index * GOLDEN_ANGLE;
      let candidate = null;
      for (let attempt = 0; attempt < 860; attempt += 1) {
        const proposed = {
          ...item,
          ...box,
          x: centerX + Math.cos(angle) * radius,
          y: centerY + Math.sin(angle) * radius * 0.72,
          fontSize: size,
          central: false,
        };
        if (inside(proposed, width, height) && !intersects(proposed, placed)) {
          candidate = proposed;
          break;
        }
        angle += 0.43;
        radius += 2.35;
      }
      // If the viewport is too small, leaving a low-frequency word out is
      // better than drawing overlapping, unreadable content.
      if (candidate) placed.push(candidate);
    });
    return placed;
  }

  global.GarayeWordCloudLayout = {
    layout,
    fontSize,
    minimumFontSize: MIN_FONT_SIZE,
    maximumFontSize: MAX_FONT_SIZE,
  };
}(window));
