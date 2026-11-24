"use strict";

const PEEK_TYPES = ["held", "wide_swing", "slow_peek", "jump_peek", "ability_peek", "jiggle", "re_peek", "other"];

function recordRight(state, key, ruleType) {
    return recordCorrected(state, key, ruleType);
}

function recordCorrected(state, key, type) {
    if (PEEK_TYPES.includes(type)) {
        Object.defineProperty(state, key, {value: type, writable: true, enumerable: true, configurable: true});
    }
    return state;
}

function loadAnswers(storage, key) {
    const state = {};
    try {
        const parsed = JSON.parse(storage.getItem(key));
        if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
            for (const [answerKey, type] of Object.entries(parsed)) recordCorrected(state, answerKey, type);
        }
    } catch (_) { /* Storage may be unavailable, full or contain invalid JSON. */ }
    return state;
}

function saveAnswers(storage, key, state) {
    try {
        storage.setItem(key, exportAnswers(state));
        return true;
    } catch (_) {
        return false;
    }
}

function exportAnswers(state) {
    const clean = {};
    for (const key of Object.keys(state).sort()) recordCorrected(clean, key, state[key]);
    return JSON.stringify(clean, null, 2);
}

function validPoint(point) {
    return point && point[0] !== null && point[1] !== null &&
        Number.isFinite(point[0]) && Number.isFinite(point[1]);
}

function cropPaths(paths) {
    const points = [...paths.player, ...paths.opponent].map(p => p.slice(1, 3));
    points.push(...paths.at_ref);
    const good = points.filter(validPoint);
    if (!good.length) return null;
    const xs = good.map(p => p[0]), ys = good.map(p => p[1]);
    const x0 = Math.min(...xs), x1 = Math.max(...xs);
    const y0 = Math.min(...ys), y1 = Math.max(...ys);
    const size = Math.max(x1 - x0, y1 - y0) + 20 / paths.m_per_px;
    return {x: (x0 + x1 - size) / 2, y: (y0 + y1 - size) / 2, size};
}

function pointAt(track, time) {
    const i = Math.round((time - (track[0] || [0])[0]) * 16);
    return track[i] ? track[i].slice(1, 3) : null;
}

function pathCommands(track) {
    const commands = [];
    let connected = false, segment;
    for (const sample of track) {
        const point = sample.slice(1, 3);
        if (!validPoint(point)) { connected = false; continue; }
        const join = connected && sample[3] === segment;
        commands.push([join ? "lineTo" : "moveTo", ...point]);
        connected = true;
        segment = sample[3];
    }
    return commands;
}

function sketch(canvas, card, image, time) {
    const ctx = canvas.getContext("2d"), paths = card.paths;
    ctx.fillStyle = "#0a1018";
    ctx.fillRect(0, 0, 240, 240);
    const crop = paths && cropPaths(paths);
    if (!crop) {
        ctx.fillStyle = "#eee";
        ctx.fillText("No path samples available", 12, 120);
        return;
    }
    const k = 240 / crop.size;
    const xy = p => [(p[0] - crop.x) * k, (p[1] - crop.y) * k];
    if (image && image.complete && image.naturalWidth) {
        // The PNG covers the same square as the engine. Never rotate it.
        const worldWidth = 1 / paths.px_to_image;
        ctx.drawImage(image, -crop.x * k, -crop.y * k, worldWidth * k, worldWidth * k);
    }
    const colors = ["#55d9ff", "#ffb663"];
    [paths.player, paths.opponent].forEach((track, index) => {
        ctx.beginPath();
        ctx.strokeStyle = colors[index];
        ctx.lineWidth = 2;
        for (const [command, x, y] of pathCommands(track)) {
            ctx[command](...xy([x, y]));
        }
        ctx.stroke();
    });
    // The line always represents the measured onset, even during playback.
    if (paths.sight_line && paths.at_ref.every(validPoint)) {
        ctx.beginPath();
        ctx.strokeStyle = "#eee";
        ctx.setLineDash([4, 4]);
        ctx.moveTo(...xy(paths.at_ref[0]));
        ctx.lineTo(...xy(paths.at_ref[1]));
        ctx.stroke();
        ctx.setLineDash([]);
    }
    const dots = time === null ? paths.at_ref :
        [pointAt(paths.player, time), pointAt(paths.opponent, time)];
    dots.forEach((point, index) => {
        if (!validPoint(point)) return;
        ctx.beginPath();
        ctx.fillStyle = colors[index];
        ctx.arc(...xy(point), 4, 0, 2 * Math.PI);
        ctx.fill();
    });
    ctx.fillStyle = "#fff";
    ctx.font = "12px system-ui";
    ctx.fillText("Player: blue · Opponent: orange", 6, 16);
    ctx.fillText(`${(time === null ? card.t_ref : time).toFixed(2)} s`, 6, 234);
}

function initPeekPage() {
    const data = JSON.parse(document.getElementById("peek-data").textContent);
    const storageKey = "valo-peeks:answers:v1";
    let storage = null;
    try { storage = window.localStorage; } catch (_) { /* File pages can deny storage. */ }
    const answers = loadAnswers(storage, storageKey);
    const articles = [...document.querySelectorAll("article[data-key]")];
    function count() {
        const n = data.cards.filter(card => Object.hasOwn(answers, card.key)).length;
        document.getElementById("answer-count").textContent = `${n} of ${data.cards.length} examples answered`;
    }
    data.cards.forEach((card, index) => {
        const article = articles[index], canvas = article.querySelector("canvas");
        const correction = article.querySelector(".correction"), status = article.querySelector(".answer");
        function showAnswer() {
            const type = answers[card.key];
            status.textContent = type ? (type === card.row.type ? "Right" : `Corrected: ${type.replaceAll("_", " ")}`) : "Unanswered";
            correction.value = type && type !== card.row.type ? type : "";
        }
        function changed() {
            saveAnswers(storage, storageKey, answers);
            showAnswer();
            count();
        }
        article.querySelector(".right").addEventListener("click", () => {
            recordRight(answers, card.key, card.row.type);
            changed();
        });
        correction.addEventListener("change", () => {
            if (!correction.value) return;
            recordCorrected(answers, card.key, correction.value);
            changed();
        });
        showAnswer();
        let image = null, frame = null;
        if (card.map && card.paths) {
            image = new Image();
            image.onload = () => { if (frame === null) sketch(canvas, card, image, null); };
            image.onerror = () => { if (frame === null) sketch(canvas, card, null, null); };
            image.src = card.map;
        }
        sketch(canvas, card, image, null);
        const play = article.querySelector(".play");
        play.disabled = !card.paths || !cropPaths(card.paths);
        play.addEventListener("click", () => {
            if (frame !== null) {
                cancelAnimationFrame(frame);
                frame = null;
                play.textContent = "Play";
                sketch(canvas, card, image, null);
                return;
            }
            play.textContent = "Stop";
            const began = performance.now();
            function animate(now) {
                const elapsed = (now - began) / 1000;
                if (elapsed >= 3) {
                    frame = null;
                    play.textContent = "Play";
                    sketch(canvas, card, image, null);
                    return;
                }
                sketch(canvas, card, image, card.t_ref - 2 + elapsed);
                frame = requestAnimationFrame(animate);
            }
            frame = requestAnimationFrame(animate);
        });
    });
    count();
    document.getElementById("export").addEventListener("click", () => {
        const url = URL.createObjectURL(new Blob([exportAnswers(answers)], {type: "application/json"}));
        const link = document.createElement("a");
        link.href = url;
        link.download = "answers.json";
        document.body.appendChild(link);
        link.click();
        link.remove();
        setTimeout(() => URL.revokeObjectURL(url), 1000);
    });
}

if (typeof document !== "undefined") initPeekPage();
if (typeof module !== "undefined") module.exports = {
    recordRight, recordCorrected, loadAnswers, saveAnswers, exportAnswers, cropPaths, pointAt, pathCommands
};
