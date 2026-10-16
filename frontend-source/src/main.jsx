import React, { memo, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { Dialog } from "@base-ui/react/dialog";
import { AnimatePresence, motion } from "motion/react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeHighlight from "rehype-highlight";
import {
  Activity, AlertCircle, ArrowDown, ArrowUp, ArrowUpRight, Check, ChevronDown, ChevronRight, Clipboard, Copy, Download,
  ExternalLink, Eye, File, FileImage, FileText, FolderOpen, ImagePlus, Images, Info, Layers, LoaderCircle,
  Maximize2, Menu, MessageSquare, Mic, MicOff, Moon, MoreHorizontal, PanelLeftClose, Pencil,
  Plus, RotateCcw, Search, Settings2, ShieldCheck, SlidersHorizontal, Sparkles, Square, Sun, Trash2,
  WandSparkles, X, ZoomIn, ZoomOut, Cpu, Volume2, Play, Pause, Pin, Bookmark, Bell
} from "lucide-react";
import "./styles.css";
import {DocumentWorkspace,RecoverySettings,LocalImageTools,workspaceCall,MemoryControls,ProjectWorkspace} from "./workspace.jsx";

const fileUrl = (path = "") => {
  if (!path) return "";
  if (/^(file|data|https?):/i.test(path)) return path;
  return "file:///" + path.replace(/\\/g, "/").split("/").map((part, i) => i === 0 ? part : encodeURIComponent(part)).join("/");
};
const parse = (value, fallback = null) => { try { return JSON.parse(value); } catch { return fallback; } };
const readTheme = () => { try { return localStorage.getItem("offline-gpt-theme") || "dark"; } catch { return "dark"; } };
const writeTheme = (value) => { try { localStorage.setItem("offline-gpt-theme", value); } catch { /* Python settings also persist the selected theme. */ } };
const call = (bridge, method, ...args) => new Promise((resolve) => {
  if (!bridge || !bridge[method]) return resolve(null);
  bridge[method](...args, resolve);
});
const copyLocalText = async (bridge, value) => {
  if (!value) return false;
  if (bridge?.copyText) {
    try {
      const result = parse(await call(bridge, "copyText", value), {});
      if (result?.ok === true) return true;
    } catch { /* Fall through to the browser clipboard. */ }
  }
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(value);
      return true;
    }
  } catch { /* Embedded file:// views may not expose the Clipboard API. */ }
  // QWebEngine can deny both the Qt bridge and Clipboard API in some focus
  // states. A temporary textarea provides a local, synchronous fallback.
  try {
    const textarea = document.createElement("textarea");
    textarea.value = value;
    textarea.setAttribute("readonly", "");
    textarea.style.position = "fixed";
    textarea.style.opacity = "0";
    textarea.style.pointerEvents = "none";
    document.body.appendChild(textarea);
    textarea.select();
    const copied = document.execCommand("copy");
    textarea.remove();
    return copied;
  } catch {
    return false;
  }
};

function useDesktopBridge() {
  const [bridge, setBridge] = useState(null);
  useEffect(() => {
    if (window.qt?.webChannelTransport && window.QWebChannel) {
      new window.QWebChannel(window.qt.webChannelTransport, (channel) => setBridge(channel.objects.offline));
    }
  }, []);
  return bridge;
}

function BrandMark({ size = 24 }) {
  return <svg width={size} height={size} viewBox="0 0 32 32" fill="none" aria-hidden="true"><path d="M16 4C22.2 4 25 8.4 25 16S22.2 28 16 28S7 23.6 7 16S9.8 4 16 4Z" stroke="currentColor" strokeWidth="5"/></svg>;
}

function IconButton({ title, children, onClick, className = "", disabled = false }) {
  return <button type="button" className={"icon-button " + className} title={title} aria-label={title} onClick={onClick} disabled={disabled}>{children}</button>;
}

function useDismissiblePanel(open, setOpen, panelRef) {
  useEffect(() => {
    if (!open) return undefined;
    const onPointerDown = (event) => { if (!panelRef.current?.contains(event.target)) setOpen(false); };
    const onKeyDown = (event) => { if (event.key === "Escape") setOpen(false); };
    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open, setOpen, panelRef]);
}

function modelHealthSummary(modelHealth = {}) {
  if (["error", "failed"].includes(modelHealth.loadState) || modelHealth.testState === "failed" || (modelHealth.lastError && !modelHealth.loaded && modelHealth.testState !== "passed")) return { label: "Needs attention", tone: "error" };
  if (["loading", "testing"].includes(modelHealth.loadState) || modelHealth.testState === "testing") return { label: "Loading model", tone: "loading" };
  if (modelHealth.loaded) return { label: "Model ready", tone: "ready" };
  if (modelHealth.runtimePresent === false) return { label: "Runtime unavailable", tone: "error" };
  if (modelHealth.runtimePresent) return { label: "Runtime available", tone: "idle" };
  return { label: "Checking local model", tone: "idle" };
}

function modelDisplayName(modelHealth = {}) {
  const raw = String(modelHealth.modelName || "Local GPT-OSS").split("/").pop();
  if (/gpt[-_]?oss[-_]?20b/i.test(raw)) return "GPT-OSS 20B";
  return raw.replace(/[-_]+/g, " ");
}

function formatBytes(bytes) {
  const value = Number(bytes);
  if (!Number.isFinite(value) || value <= 0) return "";
  return value >= 1024 ** 3 ? `${(value / 1024 ** 3).toFixed(1)} GB` : `${(value / 1024 ** 2).toFixed(0)} MB`;
}

function ModelSelector({ modelHealth, onRefresh, onOpenSettings }) {
  const [open, setOpen] = useState(false);
  const panelRef = useRef(null);
  useDismissiblePanel(open, setOpen, panelRef);
  const health = modelHealthSummary(modelHealth);
  const modelName = modelDisplayName(modelHealth);
  const stateText = modelHealth.loadState ? String(modelHealth.loadState).replace(/[-_]+/g, " ") : health.label;
  const context = Number(modelHealth.effectiveContext || modelHealth.context);
  return <div className="model-selector" ref={panelRef}>
    <button type="button" className="model-chip" aria-haspopup="dialog" aria-expanded={open} onClick={() => { const next = !open; setOpen(next); if (next) onRefresh?.(); }}>
      <span className={`model-indicator state-${health.tone}`} />
      <span>{modelName}</span><ChevronDown size={13} />
    </button>
    {open && <section className="model-popover" role="dialog" aria-label="Current local model">
      <div className="popover-heading"><div><strong>{modelName}</strong><small>{modelHealth.engineName || "Local chat engine"}</small></div><span className={`health-badge state-${health.tone}`}>{health.label}</span></div>
      <div className="popover-details">
        <div><span>Runtime state</span><strong>{stateText}</strong></div>
        {context > 0 && <div><span>Context window</span><strong>{context.toLocaleString()} tokens</strong></div>}
        {modelHealth.inferenceMode && <div><span>Inference</span><strong>{modelHealth.inferenceMode}</strong></div>}
      </div>
      <p>Model IDs and engine options are managed in local Chat settings.</p>
      <button type="button" className="popover-action" onClick={() => { setOpen(false); onOpenSettings?.(); }}>Open model settings <ArrowUpRight size={14} /></button>
    </section>}
  </div>;
}

function LocalStatus({ modelHealth, onRefresh, onOpenSettings }) {
  const [open, setOpen] = useState(false);
  const panelRef = useRef(null);
  useDismissiblePanel(open, setOpen, panelRef);
  const health = modelHealthSummary(modelHealth);
  const modelName = modelDisplayName(modelHealth);
  const context = Number(modelHealth.effectiveContext || modelHealth.context);
  const freeRam = formatBytes(modelHealth.freeRamBytes);
  const totalRam = formatBytes(modelHealth.totalRamBytes);
  const freeGpu = formatBytes(modelHealth.freeGpuBytes);
  return <div className="local-status" ref={panelRef}>
    <button type="button" className="local-card" aria-haspopup="dialog" aria-expanded={open} onClick={() => { const next = !open; setOpen(next); if (next) onRefresh?.(); }}>
      <span className={`online-dot state-${health.tone}`} />
      <span className="local-card-copy"><strong>{health.label}</strong><small>{modelName} · No cloud calls</small></span>
      <ChevronRight size={15} />
    </button>
    {open && <section className="local-popover" role="dialog" aria-label="Local model status">
      <div className="popover-heading"><div><strong>Local workspace</strong><small>Private · running on this device</small></div><span className={`health-badge state-${health.tone}`}>{health.label}</span></div>
      <div className="popover-details">
        {modelHealth.modelName && <div><span>Model</span><strong title={modelHealth.modelName}>{modelHealth.modelName}</strong></div>}
        {modelHealth.engineName && <div><span>Runtime</span><strong>{modelHealth.engineName}</strong></div>}
        {context > 0 && <div><span>Context</span><strong>{context.toLocaleString()} tokens</strong></div>}
        {(freeRam || totalRam) && <div><span>Available RAM</span><strong>{freeRam || "Unavailable"}{totalRam ? ` of ${totalRam}` : ""}</strong></div>}
        {freeGpu && <div><span>Available GPU</span><strong>{freeGpu}</strong></div>}
        {modelHealth.inferenceMode && <div><span>Inference mode</span><strong>{modelHealth.inferenceMode}</strong></div>}
        {modelHealth.lastError && <div className="popover-error"><span>Last error</span><strong>{modelHealth.lastError}</strong></div>}
      </div>
      <button type="button" className="popover-action" onClick={() => { setOpen(false); onOpenSettings?.(); }}>Open model status <ArrowUpRight size={14} /></button>
    </section>}
  </div>;
}

function FilePill({ file, onRemove }) {
  const ext = (file.name || "").split(".").pop()?.toLowerCase();
  const isImage = ["png", "jpg", "jpeg", "webp", "bmp"].includes(ext);
  const Icon = isImage ? FileImage : ["pdf", "doc", "docx", "pptx", "txt", "md", "rtf"].includes(ext) ? FileText : File;
  return <div className={`file-pill ${isImage ? "file-pill-image" : ""}`}>
    {isImage ? <span className="file-thumbnail"><img src={fileUrl(file.path)} alt="" /></span> : <span className="file-icon"><Icon size={16} /></span>}
    <span className="file-meta"><strong>{file.name}</strong><small>{file.kind || ext?.toUpperCase() || "File"} · attached to this message</small></span>
    {onRemove && <IconButton title="Remove attachment" onClick={onRemove}><X size={15} /></IconButton>}
  </div>;
}

function MessageAttachment({ value, bridge }) {
  const file = typeof value === "string" ? { path: value, name: value.split(/[\\/]/).pop() } : value;
  if (/\.(png|jpe?g|webp|bmp)$/i.test(file.path || file.name || "")) return <button type="button" className="message-image-attachment" onClick={() => bridge?.openImage?.(file.path)} title="Open attached image"><img loading="lazy" decoding="async" src={fileUrl(file.path)} alt={file.name || "Attached image"} /><span>{file.name}</span></button>;
  return <FilePill file={{ ...file, kind: file.kind || "Attached file" }} />;
}

function groupChatsByDate(chatList) {
  const groups = {
    pinned: [],
    today: [],
    yesterday: [],
    previous7Days: [],
    previous30Days: [],
    older: [],
  };

  const now = new Date();
  const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
  const startOfYesterday = startOfToday - 86400000;
  const startOf7Days = startOfToday - 6 * 86400000;
  const startOf30Days = startOfToday - 29 * 86400000;

  for (const chat of chatList) {
    if(chat.pinned){groups.pinned.push(chat);continue;}
    let t = chat.updated;
    if (typeof t === "number") {
      if (t < 10000000000) t = t * 1000;
    } else if (typeof t === "string") {
      const parsed = Date.parse(t);
      if (!isNaN(parsed)) t = parsed;
      else {
        const num = Number(t);
        t = !isNaN(num) ? (num < 10000000000 ? num * 1000 : num) : 0;
      }
    } else {
      t = 0;
    }

    if (t >= startOfToday) {
      groups.today.push(chat);
    } else if (t >= startOfYesterday) {
      groups.yesterday.push(chat);
    } else if (t >= startOf7Days) {
      groups.previous7Days.push(chat);
    } else if (t >= startOf30Days) {
      groups.previous30Days.push(chat);
    } else {
      groups.older.push(chat);
    }
  }

  const sections = [];
  if(groups.pinned.length)sections.push({title:"Pinned",items:groups.pinned});
  if (groups.today.length) sections.push({ title: "Today", items: groups.today });
  if (groups.yesterday.length) sections.push({ title: "Yesterday", items: groups.yesterday });
  if (groups.previous7Days.length) sections.push({ title: "Previous 7 Days", items: groups.previous7Days });
  if (groups.previous30Days.length) sections.push({ title: "Previous 30 Days", items: groups.previous30Days });
  if (groups.older.length) sections.push({ title: "Older", items: groups.older });

  return sections;
}

function imagePreviewStyle(dimensions = "") {
  const match=String(dimensions).match(/(\d+)\s*[×x]\s*(\d+)/);
  const ratio=match && Number(match[2])>0 ? Number(match[1])/Number(match[2]) : 16/9;
  return {"--image-ratio":ratio, aspectRatio:String(ratio)};
}

function LuminousDotField({ paused = false }) {
  const canvasRef = useRef(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d", { alpha: false });
    if (!ctx) return;

    let animId = null;
    let startTime = performance.now();
    const prefersReducedMotion = window.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches;

    const render = (now) => {
      if (paused) return;
      const t = prefersReducedMotion ? 0 : (now - startTime) * 0.001;

      const rect = canvas.getBoundingClientRect();
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      const w = Math.max(10, Math.floor(rect.width * dpr));
      const h = Math.max(10, Math.floor(rect.height * dpr));

      if (canvas.width !== w || canvas.height !== h) {
        canvas.width = w;
        canvas.height = h;
      }

      // Obsidian background
      ctx.fillStyle = "#07090D";
      ctx.fillRect(0, 0, w, h);

      // Grid dimensions
      const spacing = 12 * dpr;
      const dotRadius = 1.35 * dpr;
      const cols = Math.ceil(w / spacing) + 1;
      const rows = Math.ceil(h / spacing) + 1;
      const startX = (w - (cols - 1) * spacing) / 2;
      const startY = (h - (rows - 1) * spacing) / 2;
      const cx = w * 0.5;
      const cy = h * 0.5;
      const maxDist = Math.hypot(cx, cy) || 1;

      for (let r = 0; r < rows; r++) {
        const y = startY + r * spacing;
        for (let c = 0; c < cols; c++) {
          const x = startX + c * spacing;

          // Diagonal wave 1 (30 deg)
          const p1 = (x * 0.866 + y * 0.5) / (85 * dpr) - t * 2.2;
          const w1 = Math.sin(p1) * 0.5 + 0.5;

          // Diagonal counter-wave 2 (-40 deg)
          const p2 = (x * 0.766 - y * 0.642) / (120 * dpr) + t * 1.5;
          const w2 = Math.cos(p2) * 0.5 + 0.5;

          // Radial expansion wave from center
          const dist = Math.hypot(x - cx, y - cy);
          const p3 = dist / (95 * dpr) - t * 2.6;
          const w3 = Math.cos(p3) * 0.5 + 0.5;

          // Center vignette
          const vignette = 1 - Math.min(1, Math.pow(dist / maxDist, 1.8) * 0.6);

          // Monochrome brightness: soft grey to brilliant white
          const lum = Math.min(0.98, Math.max(0.08, (0.10 + w1 * 0.40 + w2 * 0.30 + w3 * 0.20) * vignette));
          const grey = Math.round(lum * 255);

          ctx.fillStyle = `rgb(${grey}, ${grey}, ${grey})`;
          ctx.beginPath();
          ctx.arc(x, y, dotRadius, 0, Math.PI * 2);
          ctx.fill();
        }
      }

      if (!prefersReducedMotion && !paused) {
        animId = requestAnimationFrame(render);
      }
    };

    animId = requestAnimationFrame(render);

    return () => {
      if (animId) cancelAnimationFrame(animId);
    };
  }, [paused]);

  return <canvas ref={canvasRef} className="luminous-canvas" aria-hidden="true" />;
}

function ImageGenerationProgress({ status = "", prompt = "", userPrompt = "", dimensions, progress, bridge, elapsedSeconds = 0 }) {
  const surface = useRef(null);
  const [visible, setVisible] = useState(true);

  useEffect(() => {
    let inView = true;
    const update = () => setVisible(inView && !document.hidden);
    const observer = new IntersectionObserver((entries) => {
      inView = entries[0]?.isIntersecting;
      update();
    });
    if (surface.current) observer.observe(surface.current);
    document.addEventListener("visibilitychange", update);
    return () => {
      observer.disconnect();
      document.removeEventListener("visibilitychange", update);
    };
  }, []);

  const states = {
    QUEUED: "Waiting to begin",
    PREPARING: "Preparing your image",
    LOADING_MODEL: "Loading local image engine",
    SAMPLING: "Creating the composition",
    DECODING: "Revealing the details",
    UPSCALING: "Refining the resolution",
    SAVING: "Saving your image",
    COMPLETED: "Your image is ready",
    FAILED: "Generation failed",
    CANCELLED: "Stopped"
  };

  const state = progress?.state || "PREPARING";
  const percent = ["SAMPLING", "UPSCALING"].includes(state) && Number.isFinite(progress?.percent) ? Math.min(99, progress.percent) : null;
  const stepInfo = progress?.completedSteps && progress?.totalSteps ? `Step ${progress.completedSteps} / ${progress.totalSteps}` : null;

  return (
    <div className="image-gen-card" ref={surface} style={imagePreviewStyle(dimensions)} role="status" data-image-state={state} data-motion={visible ? "running" : "paused"}>
      <div className="generation-surface">
        <LuminousDotField paused={!visible} />
        <div className="generation-topline">
          <span>{states[state] || "Preparing your image"}</span>
          <IconButton title="Stop image generation" onClick={() => bridge?.stop?.()}>
            <Square size={13} fill="currentColor" />
          </IconButton>
        </div>
        <div className="generation-bottomline">
          <time>{formatDuration(elapsedSeconds)}</time>
          {stepInfo ? (
            <span className="stage-percentage">{stepInfo}</span>
          ) : percent != null ? (
            <span className="stage-percentage">{percent}% <small>{state === "SAMPLING" ? "sampling" : "upscaling"}</small></span>
          ) : (
            <span className="generation-indeterminate" aria-label="Progress is indeterminate">
              Working <ActivityIndicator />
            </span>
          )}
        </div>
        {percent != null && (
          <div className="image-gen-progress-track">
            <div role="progressbar" aria-label="Current image stage progress" aria-valuemin="0" aria-valuemax="100" aria-valuenow={percent} className="image-gen-progress-fill" style={{ width: `${percent}%` }} />
          </div>
        )}
      </div>
      <details className="generation-details">
        <summary>Generation details</summary>
        <p>{status}</p>
        {dimensions && <p>Output: {dimensions}</p>}
        {prompt && <p>{prompt}</p>}
        {userPrompt && userPrompt !== prompt && <p>Request: {userPrompt}</p>}
      </details>
    </div>
  );
}

function ImageLightbox({ imagePath, dimensions, onClose, onSave, onCopy, onReveal }) {
  const [zoom, setZoom] = useState(1);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    const handleKeyDown = (e) => {
      if (e.key === "Escape") onClose();
      if (e.key === "+" || e.key === "=") setZoom((z) => Math.min(4, +(z + 0.25).toFixed(2)));
      if (e.key === "-") setZoom((z) => Math.max(0.5, +(z - 0.25).toFixed(2)));
      if (e.key === "0") setZoom(1);
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [onClose]);

  const handleCopy = async () => {
    if (onCopy) {
      await onCopy(imagePath);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    }
  };

  return (
    <div className="lightbox-backdrop" onClick={onClose}>
      <div className="lightbox-dialog" onClick={(e) => e.stopPropagation()}>
        <div className="lightbox-topbar">
          <div className="lightbox-info">
            <span className="lightbox-filename">{imagePath.split(/[\\/]/).pop()}</span>
            {dimensions && <span className="lightbox-dimensions">{dimensions}</span>}
          </div>
          <div className="lightbox-controls">
            <button type="button" className="lightbox-btn" title="Zoom in (+)" onClick={() => setZoom((z) => Math.min(4, +(z + 0.25).toFixed(2)))}>
              <ZoomIn size={15} />
            </button>
            <span className="lightbox-zoom-indicator">{Math.round(zoom * 100)}%</span>
            <button type="button" className="lightbox-btn" title="Zoom out (-)" onClick={() => setZoom((z) => Math.max(0.5, +(z - 0.25).toFixed(2)))}>
              <ZoomOut size={15} />
            </button>
            <button type="button" className="lightbox-btn" title="Reset zoom (0)" onClick={() => setZoom(1)}>
              1:1
            </button>
            <div className="lightbox-divider" />
            <button type="button" className="lightbox-btn" title="Copy image bitmap" onClick={handleCopy}>
              {copied ? <Check size={15} className="text-success" /> : <Copy size={15} />}
              <span>{copied ? "Copied" : "Copy"}</span>
            </button>
            <button type="button" className="lightbox-btn" title="Save copy to disk" onClick={() => onSave?.(imagePath)}>
              <Download size={15} />
              <span>Save As</span>
            </button>
            <button type="button" className="lightbox-btn" title="Show in Windows Explorer" onClick={() => onReveal?.(imagePath)}>
              <FolderOpen size={15} />
              <span>Folder</span>
            </button>
            <button type="button" className="lightbox-btn lightbox-close" title="Close (Esc)" onClick={onClose}>
              <X size={16} />
            </button>
          </div>
        </div>
        <div className="lightbox-viewport">
          <img
            src={fileUrl(imagePath)}
            alt={imagePath.split(/[\\/]/).pop()}
            style={{ transform: `scale(${zoom})`, transition: "transform 0.15s ease-out" }}
            className="lightbox-image"
          />
        </div>
      </div>
    </div>
  );
}

function ImageResultCard({ message, bridge, onOpenLightbox, canRegenerate, onPrefillPrompt }) {
  const imageArrival=useRef(performance.now()),imagePaintReported=useRef(false);
  const paths = message.images?.length ? message.images : [message.attachment || message.path || ""].filter(Boolean);
  const [copiedPath, setCopiedPath] = useState(null);
  const [promptOpen, setPromptOpen] = useState(false);
  const [promptCopied, setPromptCopied] = useState(false);

  if (!paths.length) return null;

  const promptText = message.prompt || "";

  const handleCopyImage = async (path, e) => {
    e?.stopPropagation();
    if (!path) return;
    try {
      const raw = await call(bridge, "copyImage", path);
      const res = parse(raw, {});
      if (res?.ok) {
        setCopiedPath(path);
        window.setTimeout(() => setCopiedPath(null), 1500);
      }
    } catch {}
  };

  const handleReveal = (path, e) => {
    e?.stopPropagation();
    bridge?.revealFile?.(path);
  };

  const handleSave = (path, e) => {
    e?.stopPropagation();
    bridge?.saveImage?.(path);
  };

  const handleCopyPrompt = async (e) => {
    e?.stopPropagation();
    if (!promptText) return;
    try {
      if (!await copyLocalText(bridge, promptText)) return;
      setPromptCopied(true);
      window.setTimeout(() => setPromptCopied(false), 1500);
    } catch {}
  };

  const dims = message.dimensions || "";
  const parsedDimensions = dims.match(/(\d+)\s*[×x]\s*(\d+)/);
  const is4K = parsedDimensions && Number(parsedDimensions[1]) * Number(parsedDimensions[2]) >= 3840 * 2160;
  const engineLabel = message.metrics?.model || (message.imageAction === "resize_image" ? "Local image resize" : message.imageAction === "enhance_image" ? "Local image enhancement" : "FLUX.2 Klein 9B");
  const imageFormat = /\.(jpe?g)$/i.test(paths[0]) ? "JPEG" : /\.webp$/i.test(paths[0]) ? "WebP" : "PNG";

  return (
    <div className="image-result-card" style={imagePreviewStyle(message.dimensions)}>
      <div className="image-result-header">
        <div className="image-result-badge-group">
          <span className="model-chip"><FileImage size={13} /> {engineLabel}</span>
          {is4K && <span className="format-chip">4K output</span>}
          <span className="format-chip">{imageFormat}</span>
          {dims && <span className="dimension-chip">{message.metrics?.native_dimensions && (message.metrics?.upscaled || dims !== message.metrics.native_dimensions) ? `${dims} (Native ${message.metrics.native_dimensions} · ${message.metrics.upscale_method||'resized'})` : dims}</span>}
        </div>
      </div>

      <div className="image-result-gallery">
        {paths.map((path) => (
          <div className="image-result-frame" key={path}>
            <div className="image-preview-wrapper" onClick={() => onOpenLightbox?.(path, dims)}>
              <div className="image-dot-field image-result-reveal" aria-hidden="true"><span/><i/></div>
              <img
                loading="lazy"
                decoding="async"
                src={fileUrl(path)}
                alt={path.split(/[\\/]/).pop() || "Generated result"}
                onLoad={(e)=>{e.currentTarget.classList.add("image-loaded");e.currentTarget.parentElement.classList.add("image-revealed");if(!imagePaintReported.current){imagePaintReported.current=true;requestAnimationFrame(()=>requestAnimationFrame(()=>bridge?.recordRendering?.(JSON.stringify({requestId:message.requestId||message.id,imageRenderDelayMs:performance.now()-imageArrival.current}))));}}}
                className="image-preview"
              />
              <div className="image-hover-overlay">
                <button type="button" className="overlay-btn" title="View Fullscreen" onClick={(e) => { e.stopPropagation(); onOpenLightbox?.(path, dims); }}>
                  <Maximize2 size={15} /> <span>Inspect</span>
                </button>
                <button type="button" className="overlay-btn" title="Copy image bitmap" onClick={(e) => handleCopyImage(path, e)}>
                  {copiedPath === path ? <Check size={15} className="text-success" /> : <Copy size={15} />} <span>{copiedPath === path ? "Copied" : "Copy"}</span>
                </button>
              </div>
            </div>

            <div className="image-result-toolbar">
              <span className="image-filename" title={path}>{path.split(/[\\/]/).pop()}</span>
              <div className="image-actions-group">
                <button type="button" className="image-action-btn" title="Copy Image to Clipboard" onClick={(e) => handleCopyImage(path, e)}>
                  {copiedPath === path ? <Check size={13} className="text-success" /> : <Copy size={13} />}
                  <span>{copiedPath === path ? "Copied" : "Copy Image"}</span>
                </button>
                <button type="button" className="image-action-btn" title="Save As…" onClick={(e) => handleSave(path, e)}>
                  <Download size={13} />
                  <span>Save As</span>
                </button>
                <button type="button" className="image-action-btn" title="Show in Folder" onClick={(e) => handleReveal(path, e)}>
                  <FolderOpen size={13} />
                  <span>Folder</span>
                </button>
                <button type="button" className="image-action-btn" title="Create variation" onClick={() => onPrefillPrompt?.(`Create a variation of this image with varied composition and lighting: ${message.userPrompt || message.prompt || ""}`)}>
                  <SlidersHorizontal size={13} />
                  <span>Variation</span>
                </button>
                {canRegenerate && <button type="button" className="image-action-btn" title="Regenerate image" onClick={()=>bridge?.retryLastMessage?.()}><RotateCcw size={13}/><span>Regenerate</span></button>}
                <button type="button" className="image-action-btn" title="Expand Fullscreen" onClick={(e) => { e.stopPropagation(); onOpenLightbox?.(path, dims); }}>
                  <Maximize2 size={13} />
                </button>
              </div>
            </div>
          </div>
        ))}
      </div>

      {message.notice && <div className="image-result-notice"><Info size={14} /><span>{message.notice}</span></div>}

      {promptText && (
        <div className="image-prompt-accordion">
          <button type="button" className="prompt-accordion-toggle" onClick={() => setPromptOpen((o) => !o)}>
            <div className="accordion-label">
              <Info size={13} />
              <span>Generation details</span>
            </div>
            <ChevronDown size={14} className={`accordion-chevron ${promptOpen ? "open" : ""}`} />
          </button>
          {promptOpen && (
            <div className="prompt-accordion-body">{dims && <p className="image-original-request-text">{dims} · {imageFormat}{message.metrics?.upscaled ? " · Upscaled" : ""}{message.metrics?.operation_route ? ` · Route: ${message.metrics.operation_route}` : ""}</p>}
              {message.metrics?.operation_route && (
                <p className="image-original-request-text"><span>Operation Route</span>{message.metrics.operation_route} {message.metrics.diffusion_steps != null ? `(${message.metrics.diffusion_steps} diffusion steps)` : ""}</p>
              )}
              {message.metrics?.image_edit_method && (
                <p className="image-original-request-text"><span>Method</span>{message.metrics.image_edit_method}</p>
              )}
              {message.userPrompt && <p className="image-original-request-text"><span>Original request</span>{message.userPrompt}</p>}
              <p className="prompt-text">{promptText}</p>
              <button type="button" className="prompt-copy-btn" onClick={handleCopyPrompt} title="Copy prompt">
                {promptCopied ? <Check size={13} /> : <Copy size={13} />}
                <span>{promptCopied ? "Copied" : "Copy Prompt"}</span>
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function ImageDetailsViewer({ image, onClose, bridge, onSelectChat, onPrefillPrompt, onOpenLightbox, onDeleted, onVariationStarted }) {
  const [copiedImage, setCopiedImage] = useState(false);
  const [copiedPrompt, setCopiedPrompt] = useState(false);
  const [deleting, setDeleting] = useState(false);

  useEffect(() => {
    const handleKeyDown = (e) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [onClose]);

  if (!image) return null;

  const imagePath = image.path || image.image_path || "";
  const filename = imagePath.split(/[\\/]/).pop() || "image.png";
  const userPromptText = image.userPrompt || image.user_prompt || "";
  const plannedPromptText = image.prompt || image.planned_prompt || "";
  const dims = image.finalDimensions || image.dimensions || "";
  const nativeDims = image.nativeDimensions || image.native_dimensions || "";
  const isUpscaled = !!(image.upscaled || (nativeDims && dims && nativeDims !== dims));
  const honestDims = isUpscaled && nativeDims ? `${dims} (Native ${nativeDims} · ${image.generationSettings?.upscalingMethod || "Upscaler unavailable"})` : dims;
  const dateStr = image.created ? new Date(image.created * 1000).toLocaleString() : "";
  const fileSizeStr = image.fileSize ? `${(image.fileSize / (1024 * 1024)).toFixed(2)} MB` : "";

  const handleCopyImage = async () => {
    if (!imagePath) return;
    try {
      const raw = await call(bridge, "copyImage", imagePath);
      const res = parse(raw, {});
      if (res?.ok) {
        setCopiedImage(true);
        window.setTimeout(() => setCopiedImage(false), 1500);
      }
    } catch {}
  };

  const handleCopyPrompt = async () => {
    const text = plannedPromptText || userPromptText;
    if (!text) return;
    try {
      if (await copyLocalText(bridge, text)) {
        setCopiedPrompt(true);
        window.setTimeout(() => setCopiedPrompt(false), 1500);
      }
    } catch {}
  };

  const handleSave = () => {
    if (imagePath) bridge?.saveImage?.(imagePath);
  };

  const handleReveal = () => {
    if (imagePath) bridge?.revealFile?.(imagePath);
  };

  const handleVariation = () => {
    const p = userPromptText || plannedPromptText;
    onPrefillPrompt?.(`Create a variation of this image with varied composition and lighting: ${p}`);
    onClose();
  };

  const handleOpenChat = () => {
    const cid = image.chatId || image.chat_id;
    if (cid) {
      onSelectChat?.(cid);
      onClose();
    }
  };

  const handleDelete = async (deleteFile = false) => {
    const promptMsg = deleteFile
      ? "Move this image file to Windows Recycle Bin? Shared files are protected."
      : "Move this image to Library Trash? Original files and version metadata will remain recoverable.";
    if (!window.confirm(promptMsg)) return;
    setDeleting(true);
    try {
      const result=parse(await call(bridge, "deleteLibraryImage", image.id, deleteFile),{});
      if(!result.ok) throw new Error(result.error||"The Library item could not be removed; its record was retained.");
      onDeleted?.(image.id);
      onClose();
    } catch (e) {
      window.alert(e.message);
    } finally {
      setDeleting(false);
    }
  };

  return (
    <div className="image-details-modal" onClick={onClose} role="dialog" aria-label="Image details">
      <div className="image-details-main" onClick={(e) => e.stopPropagation()}>
        <div className="image-details-topbar">
          <span style={{ fontSize: 13, fontWeight: 600, color: "rgb(var(--foreground))" }}>{filename}</span>
          <button type="button" className="details-btn" onClick={onClose} title="Close (Esc)"><X size={16} /></button>
        </div>
        <div className="image-details-viewport" onClick={() => onOpenLightbox?.(imagePath, dims)} style={{ cursor: "zoom-in" }}>
          <img src={fileUrl(imagePath)} alt={filename} />
        </div>
      </div>
      <div className="image-details-sidebar" onClick={(e) => e.stopPropagation()}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <h3>Image Specifications</h3>
          <button type="button" className="details-btn" style={{ padding: "4px 8px" }} onClick={onClose}><X size={15} /></button>
        </div>

        {userPromptText && (
          <div>
            <div className="details-section-title">User Request</div>
            <div className="details-text-box">{userPromptText}</div>
          </div>
        )}

        {plannedPromptText && (
          <div>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 6 }}>
              <div className="details-section-title" style={{ margin: 0 }}>Creative Director Prompt</div>
              <button type="button" className="details-btn" style={{ padding: "2px 8px", fontSize: 11 }} onClick={handleCopyPrompt}>
                {copiedPrompt ? <Check size={12} className="text-success" /> : <Copy size={12} />}
                <span>{copiedPrompt ? "Copied" : "Copy"}</span>
              </button>
            </div>
            <div className="details-text-box">{plannedPromptText}</div>
          </div>
        )}

        <div>
          <div className="details-section-title">Technical Specifications</div>
          <div className="details-meta-grid">
            <div className="details-meta-item"><span>Prompt mode</span><strong>{{original:"Original Prompt",smart_enhance:"Smart Enhance",creative_direction:"Creative Direction",creative:"Creative Direction",creative_director:"Creative Direction"}[image.promptMode] || "Not recorded"}</strong></div>
            <div className="details-meta-item"><span>Quality preset</span><strong>{{fast:"Fast",balanced:"Balanced",quality:"Quality"}[image.qualityPreset] || "Not recorded"}</strong></div>
            {Number.isFinite(image.generationDurationSeconds) && <div className="details-meta-item"><span>Generation duration</span><strong>{formatDuration(image.generationDurationSeconds)}</strong></div>}
            <div className="details-meta-item">
              <span>Resolution</span>
              <strong>{honestDims || "Standard"}</strong>
            </div>
            <div className="details-meta-item">
              <span>Model Engine</span>
              <strong>{image.model || "Unavailable"}</strong>
            </div>
            {isUpscaled && (
              <div className="details-meta-item">
                <span>Upscaler</span>
                <strong>{image.generationSettings?.upscalingMethod || "Unavailable"}</strong>
              </div>
            )}
            {image.seed != null && image.seed !== "" && image.seed !== -1 && (
              <div className="details-meta-item">
                <span>Seed</span>
                <strong>{image.seed}</strong>
              </div>
            )}
            {image.steps && (
              <div className="details-meta-item">
                <span>Steps</span>
                <strong>{image.steps}</strong>
              </div>
            )}
            {fileSizeStr && (
              <div className="details-meta-item">
                <span>File Size</span>
                <strong>{fileSizeStr}</strong>
              </div>
            )}
            {dateStr && (
              <div className="details-meta-item" style={{ gridColumn: "span 2" }}>
                <span>Generated At</span>
                <strong>{dateStr}</strong>
              </div>
            )}
          </div>
        </div>

        <LocalImageTools key={(image.id||image.path)+':'+(image.maskPath||'')} bridge={bridge} image={image} onUpdated={()=>bridge?.refreshLibrary?.(false)} onVariationStarted={()=>{onClose();onVariationStarted?.();}}/>
        <div className="details-action-buttons">
          <button type="button" className="details-btn primary" onClick={handleCopyImage}>
            {copiedImage ? <Check size={14} /> : <Copy size={14} />}
            <span>{copiedImage ? "Copied to Clipboard" : "Copy Image"}</span>
          </button>
          <button type="button" className="details-btn" onClick={handleSave}>
            <Download size={14} />
            <span>Save As…</span>
          </button>
          <button type="button" className="details-btn" onClick={handleReveal}>
            <FolderOpen size={14} />
            <span>Show in Windows Folder</span>
          </button>
          <button type="button" className="details-btn" onClick={handleVariation}>
            <SlidersHorizontal size={14} />
            <span>Create Variation</span>
          </button>
          {(image.chatId || image.chat_id) && (
            <button type="button" className="details-btn" onClick={handleOpenChat}>
              <MessageSquare size={14} />
              <span>Open in Chat Conversation</span>
            </button>
          )}
          <button type="button" className="details-btn danger" onClick={() => handleDelete(false)} disabled={deleting}>
            <Trash2 size={14} />
            <span>Move to Trash</span>
          </button>
        </div>
      </div>
    </div>
  );
}

function LibraryImageMenu({menu,onClose,onAction}) {
  const ref=useRef(null);
  useEffect(()=>{
    ref.current?.querySelector('button')?.focus();
    const dismiss=event=>{if(!ref.current?.contains(event.target))onClose();};
    window.addEventListener('pointerdown',dismiss);window.addEventListener('resize',onClose);
    return()=>{window.removeEventListener('pointerdown',dismiss);window.removeEventListener('resize',onClose);menu.trigger?.focus();};
  },[]);
  const actions=menu.image.hidden?['Open','Show Details','Compare Versions','Download','Rename','Restore','Permanently Delete']:['Open','Edit','Rename','Download','Show Details','Compare Versions','Delete'];
  return <div ref={ref} role="menu" aria-label="Image actions" className="library-context-menu" style={{left:Math.max(8,Math.min(menu.x,window.innerWidth-230)),top:Math.max(8,Math.min(menu.y,window.innerHeight-330))}} onClick={e=>e.stopPropagation()} onKeyDown={event=>{
    const items=[...ref.current.querySelectorAll('button:not(:disabled)')],index=items.indexOf(document.activeElement);
    if(event.key==='Escape'||event.key==='Tab'){event.preventDefault();onClose();}
    if(['ArrowDown','ArrowUp','Home','End'].includes(event.key)){event.preventDefault();items[event.key==='Home'?0:event.key==='End'?items.length-1:(index+(event.key==='ArrowDown'?1:-1)+items.length)%items.length]?.focus();}
  }}>{actions.map(action=><button key={action} role="menuitem" disabled={menu.busy||(menu.image.fileExists===false&&['Open','Edit','Download'].includes(action))} onClick={()=>{onClose();onAction(action,menu.image);}}>{action}</button>)}</div>;
}

function ImageLibrary({ images = [], loading = false, onRefresh, bridge, onSelectChat, onPrefillPrompt, onOpenLightbox, onVariationStarted, requestedImage, onRequestedConsumed }) {
  const [searchQuery, setSearchQuery] = useState("");
  const [kindFilter,setKindFilter]=useState("all");
  const [menu,setMenu]=useState(null);
  const [comparison,setComparison]=useState(null);
  const [sortBy, setSortBy] = useState("newest");
  const [selectedImage, setSelectedImage] = useState(null);
  useEffect(()=>{if(requestedImage){setSelectedImage(requestedImage);onRequestedConsumed?.();}},[requestedImage]);
  const [localImages, setLocalImages] = useState(images);
  const [selectionMode,setSelectionMode]=useState(false);
  const [selectedIds,setSelectedIds]=useState([]);
  const [bulkJob,setBulkJob]=useState("");
  const [bulkMessage,setBulkMessage]=useState("");
  const [visibility,setVisibility]=useState('active');
  const [viewLoading,setViewLoading]=useState(false);
  const [recovery,setRecovery]=useState({operations:[],pendingCount:0});
  const [showRecovery,setShowRecovery]=useState(false);
  const viewRequest=useRef(0);
  const loadCurrentView=useCallback(async()=>{
    const ticket=++viewRequest.current;
    if(!bridge?.getLibraryView){setLocalImages(images);return;}
    setViewLoading(true);
    try{
      const result=parse(await call(bridge,'getLibraryView',visibility),{});
      if(ticket!==viewRequest.current)return;
      if(!result.ok)throw new Error(result.error||'Library view could not load.');
      setLocalImages(result.images||[]);setRecovery(result.recovery||{operations:[],pendingCount:0});
      setSelectedIds(ids=>ids.filter(id=>(result.images||[]).some(image=>image.id===id)));
    }catch(error){if(ticket===viewRequest.current)setBulkMessage(error.message);}
    finally{if(ticket===viewRequest.current)setViewLoading(false);}
  },[bridge,visibility,images]);

  useEffect(() => {
    loadCurrentView();
    return()=>{viewRequest.current++;};
  }, [loadCurrentView]);

  const filteredImages = useMemo(() => {
    let result = [...localImages];
    const q = searchQuery.trim().toLowerCase();
    if (q) {
      result = result.filter((img) =>
        (img.userPrompt || img.user_prompt || "").toLowerCase().includes(q) ||
        (img.prompt || img.planned_prompt || "").toLowerCase().includes(q) ||
        (img.path || img.image_path || "").toLowerCase().includes(q) ||
        [img.displayName,img.model,img.finalDimensions,img.seed,img.operation,JSON.stringify(img.generationSettings||{})].join(" ").toLowerCase().includes(q)
      );
    }

    if(kindFilter!=="all") result=result.filter(img=>kindFilter==="edited"?!!img.parentId||!!img.operation&&!/generate/i.test(img.operation):!img.parentId&&(!img.operation||/generate/i.test(img.operation)));
    if(sortBy==="name") result.sort((a,b)=>(a.displayName||a.path?.split(/[\\/]/).pop()||"").localeCompare(b.displayName||b.path?.split(/[\\/]/).pop()||""));
    else if(sortBy==="size") result.sort((a,b)=>(b.fileSize||0)-(a.fileSize||0));
    else if (sortBy === "newest") {
      result.sort((a, b) => (b.created || 0) - (a.created || 0));
    } else if (sortBy === "oldest") {
      result.sort((a, b) => (a.created || 0) - (b.created || 0));
    } else if (sortBy === "resolution") {
      const getPixels = (s) => {
        const m = String(s || "").match(/(\d+)\s*[×x]\s*(\d+)/);
        return m ? Number(m[1]) * Number(m[2]) : 0;
      };
      result.sort((a, b) => getPixels(b.finalDimensions || b.dimensions) - getPixels(a.finalDimensions || a.dimensions));
    }
    return result;
  }, [localImages, searchQuery, sortBy,kindFilter]);

  const handleDeleted = (id) => {
    setLocalImages((prev) => prev.filter((img) => img.id !== id));
  };
  const toggleSelection=id=>setSelectedIds(ids=>ids.includes(id)?ids.filter(i=>i!==id):ids.length<500?[...ids,id]:ids);
  const manageSelected=async(action,ids=selectedIds)=>{
    if(!ids.length||bulkJob)return;
    if(action==='hide'&&!window.confirm(`Move ${ids.length} selected images to Library Trash? Files, conversations and version metadata remain recoverable.`))return;
    setBulkMessage("");setBulkJob("starting");
    try {
      const {value}=await workspaceCall(bridge,'libraryManage',{ids,action},setBulkJob);
      const completed=new Set(value.completedIds||[]);
      setLocalImages(items=>items.filter(item=>!completed.has(item.id)));
      setSelectedIds(ids=>ids.filter(id=>!completed.has(id)));
      const errors=(value.results||[]).filter(result=>result.status!=='completed');
      setBulkMessage(`${value.completed}/${value.expected} completed${value.cancelled?' · Cancelled before remaining items':''}${errors.length?' · '+errors[0].error:''}.`);
      await loadCurrentView();onRefresh?.();
    } catch(error){setBulkMessage(error.message);}
    finally{setBulkJob("");}
  };
  const exportImages=async(ids=selectedIds)=>{
    if(!ids.length||bulkJob)return;
    const path=await call(bridge,'pickLibraryExportFile');if(!path)return;
    setBulkJob('starting');
    try{const {value}=await workspaceCall(bridge,'libraryExport',{ids,path},setBulkJob);setBulkMessage(`${value.count} images exported with original metadata to ${value.path}`);}
    catch(error){setBulkMessage(error.message);}finally{setBulkJob('');}
  };
  const imageAction=async(action,image)=>{
    if(action==='Delete'||action==='Restore'||action==='Permanently Delete'){await manageSelected(action==='Delete'?'hide':action==='Restore'?'restore':'purge',[image.id]);return;}
    if(action==='Open'){bridge?.openFile?.(image.path);return;}
    if(action==='Download'){bridge?.saveImage?.(image.path);return;}
    if(action==='Rename'){
      const name=window.prompt('Display name (the original filename and references are preserved)',image.displayName||image.path.split(/[\\/]/).pop());
      if(name==null)return;setBulkJob('starting');
      try{await workspaceCall(bridge,'libraryRename',{id:image.id,name},setBulkJob);setBulkMessage('Image renamed. Original file unchanged.');await loadCurrentView();onRefresh?.();}
      catch(error){setBulkMessage(error.message);}finally{setBulkJob('');}return;
    }
    if(action==='Compare Versions'){
      const result=parse(await call(bridge,'getLibraryVersions',image.id),{});
      if(!result.ok){setBulkMessage(result.error||'Versions unavailable.');return;}
      const family=result.images||[],parent=family.find(i=>i.id===image.parentId),child=family.find(i=>i.parentId===image.id);
      setComparison({images:family,before:parent?.id||image.id,after:parent?image.id:child?.id||image.id});return;
    }
    setSelectedImage(image);
    if(action==='Edit')requestAnimationFrame(()=>setTimeout(()=>document.querySelector('.local-image-tools')?.scrollIntoView({block:'center',behavior:'smooth'}),60));
  };
  const openMenu=(event,image)=>{event.preventDefault();event.stopPropagation();const rect=event.currentTarget.getBoundingClientRect();setMenu({image,x:event.type==='contextmenu'?event.clientX:rect.right-210,y:event.type==='contextmenu'?event.clientY:rect.bottom,trigger:event.currentTarget,busy:!!bulkJob});};
  const reviewOperation=async(operation,decision)=>{
    const question=decision==='leave'?'Close this review with outcome UNKNOWN while leaving every file and Library reference unchanged? The original journal evidence will be retained.':decision==='keep'
      ? 'Have you reviewed the current file? Its bytes will be checked against the original fingerprint when one was recorded. Older operations have no original fingerprint: accepting that file is your decision, not proof that recycling succeeded.'
      : 'Hide this Library reference and close this review with the recycle outcome UNKNOWN? No file will be moved, deleted or restored.';
    if(bulkJob||!window.confirm(question))return;
    setBulkJob('starting');setBulkMessage('');
    try{
      const {value}=await workspaceCall(bridge,'libraryReview',{id:operation.id,decision,confirmed:true},setBulkJob);
      setBulkMessage(decision==='leave'?'Review closed with outcome unknown. Files and Library references are unchanged.':decision==='hide'?'Reference hidden. Recycle outcome remains unknown; no files changed.':`Reference restored. ${value.identity}. No filesystem operation performed.`);
      await loadCurrentView();onRefresh?.();
    }catch(error){setBulkMessage(error.message);}
    finally{setBulkJob('');}
  };

  return (
    <div className="library-view">
      <div className="library-header">
        <div className="library-title-area">
          <h1 className="library-title">Image Library</h1>
          <span className="library-count-badge">
            {localImages.length} {visibility!=='active'?'in Trash':localImages.length === 1 ? "image" : "images"}
          </span>
        </div>

        <div className="library-controls">
          <select aria-label="Library visibility" className="library-sort-select" value={visibility} disabled={!!bulkJob} onChange={event=>{setVisibility(event.target.value);setSelectedIds([]);setSelectionMode(false);setSelectedImage(null);setBulkMessage('');}}><option value="active">Visible images</option><option value="hidden">Trash</option></select>
          <button className="details-btn" aria-expanded={showRecovery} onClick={()=>setShowRecovery(value=>!value)}>Recovery ({recovery.pendingCount||0})</button>
          <button type="button" className="details-btn" aria-pressed={selectionMode} disabled={!!bulkJob} onClick={()=>{setSelectionMode(v=>!v);setSelectedIds([]);setBulkMessage("");}}>{selectionMode?'Done selecting':'Select images'}</button>
          <div className="library-search-wrap">
            <Search size={14} />
            <input
              type="text"
              className="library-search-input"
              placeholder="Search prompts, files or metadata…"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
            />
          </div>

          <select value={sortBy} onChange={(e) => setSortBy(e.target.value)} className="library-sort-select">
            <option value="newest">Newest first</option>
            <option value="oldest">Oldest first</option>
            <option value="resolution">Highest resolution</option><option value="name">Name</option><option value="size">Largest file</option>
          </select>

          <select aria-label="Image type" className="library-sort-select" value={kindFilter} onChange={e=>setKindFilter(e.target.value)}><option value="all">All images</option><option value="generated">Generated</option><option value="edited">Edited</option></select>
          <button type="button" className="details-btn" onClick={()=>{loadCurrentView();onRefresh?.();}} disabled={loading||viewLoading||!!bulkJob} title="Refresh library">
            {loading||viewLoading ? <LoaderCircle size={14} className="spin" /> : <RotateCcw size={14} />}
            <span>Refresh</span>
          </button>
        </div>
      </div>

      {selectionMode&&<div className="library-bulk-toolbar" aria-label="Selected Library images">
        <strong aria-live="polite">{selectedIds.length} selected</strong>
        <button className="details-btn" disabled={!!bulkJob||!filteredImages.length} onClick={()=>setSelectedIds(filteredImages.slice(0,500).map(image=>image.id))}>Select visible ({Math.min(500,filteredImages.length)})</button>
        <button className="details-btn" disabled={!!bulkJob||!selectedIds.length} onClick={()=>setSelectedIds([])}>Clear selection</button>
        {visibility==='hidden'?<button className="details-btn" disabled={!!bulkJob||!selectedIds.length} onClick={()=>manageSelected('restore')}>Restore selected</button>:<button className="details-btn" disabled={!!bulkJob||!selectedIds.length} onClick={()=>manageSelected('hide')}>Move to Trash</button>}
        {visibility==='hidden'&&<button className="details-btn" disabled={!!bulkJob||!selectedIds.length} onClick={()=>manageSelected('purge')}>Permanently delete…</button>}
        <button className="details-btn" disabled={!!bulkJob||!selectedIds.length} onClick={()=>exportImages()}>Export selected ZIP…</button>
        {bulkJob&&<button className="details-btn" onClick={()=>bridge?.cancelWorkspace?.(bulkJob)}>Cancel remaining</button>}
        <small>Shared chat files and unselected versions are protected. Removing references keeps files on disk.</small>
      </div>}
      {bulkMessage&&<p className="library-bulk-message" role="status">{bulkMessage}</p>}
      {visibility==='hidden'&&<p className="library-bulk-message">Trash keeps original files, prompts and version metadata until confirmed permanent deletion. Shared conversation files and unselected versions are protected. Missing files must first be restored in Windows Recycle Bin. Pending operations must be reviewed before restoring a reference.</p>}
      {showRecovery&&<section className="library-recovery" aria-label="Library recovery operations">
        <h2>Interrupted operations</h2><p>File absence alone does not prove recycling succeeded. Recovery choices below change Library metadata only. Journal evidence is retained.</p>
        <button className="details-btn" onClick={async()=>{const result=parse(await call(bridge,'openRecycleBin'),{});if(!result.ok)setBulkMessage(result.error||'Open Windows Recycle Bin manually.');}}>Open Windows Recycle Bin</button>
        {!recovery.operations?.length&&<p>No interrupted operations are recorded.</p>}
        {recovery.truncated&&<p>Showing the first 100 operations. Resolve pending reviews and refresh to see more.</p>}
        {(recovery.operations||[]).map(operation=>{
          const pending=operation.state==='needs_review';
          return <article className="library-recovery-item" key={operation.id}>
            <strong>{operation.title}</strong><p>{operation.state} · {operation.action} · Outcome: {operation.outcome}</p>
            <p className="library-recovery-path">{operation.path}</p><p>{operation.fileExists?'A file is present; presence does not establish original identity.':'Original path is missing; recycle success is not inferred.'}</p>
            {operation.error&&<p>{operation.error}</p>}{operation.identityError&&<p role="alert">{operation.identityError}</p>}
            {pending&&<><p>{operation.hasOriginalIdentity?'Original-byte fingerprint available for verification.':'Older operation: original identity was not recorded; manually verify any restored file.'}</p>
              <button className="details-btn" disabled={!!bulkJob||!operation.fileExists||!operation.ownerMatches||!!operation.identityError} onClick={()=>reviewOperation(operation,'keep')}>Keep reviewed file & show reference</button>
              <button className="details-btn" disabled={!!bulkJob||!operation.ownerMatches} onClick={()=>reviewOperation(operation,'hide')}>Hide reference · leave outcome unknown</button>
              <button className="details-btn" disabled={!!bulkJob} onClick={()=>reviewOperation(operation,'leave')}>Close review · keep references unchanged</button>
              {operation.fileExists&&<button className="details-btn" onClick={()=>bridge?.revealFile?.(operation.path)}>Inspect current file</button>}
              {!operation.ownerMatches&&<p role="alert">Library ownership changed. Preserve this journal record for investigation; no recovery action will be applied to a different image.</p>}
            </>}
          </article>;
        })}
      </section>}

      <div className="library-grid-scroll">
        {filteredImages.length === 0 ? (
          <div className="library-empty">
            <FileImage size={42} style={{ opacity: 0.4 }} />
            <p>{searchQuery ? "No images match your search query." : visibility==='hidden'?'Trash is empty.':'Generated images will automatically appear in your local library.'}</p>
          </div>
        ) : (
          <div className="library-grid">
            {filteredImages.map((img) => {
              const imagePath = img.path || img.image_path || "";
              const thumbSrc = fileUrl(img.thumbnail || img.thumbnail_path || imagePath);
              const promptDisplay = img.displayName || img.userPrompt || img.user_prompt || img.prompt || img.planned_prompt || imagePath.split(/[\\/]/).pop();
              const dims = img.finalDimensions || img.dimensions || "";
              const nativeDims = img.nativeDimensions || img.native_dimensions || "";
              const isUpscaled = img.upscaled || (nativeDims && dims && nativeDims !== dims);

              return (
                <div
                  key={img.id || imagePath}
                  className="library-card" role="button" tabIndex={0} aria-label={promptDisplay} onContextMenu={event=>openMenu(event,img)} onKeyDown={event=>{if(event.target!==event.currentTarget)return;if(event.key==='Enter'||event.key===' '){event.preventDefault();selectionMode?toggleSelection(img.id):setSelectedImage(img);}if(event.key==='ContextMenu'||event.key==='F10'&&event.shiftKey)openMenu(event,img);}}
                  onClick={() => selectionMode&&!bulkJob?toggleSelection(img.id):!selectionMode&&setSelectedImage(img)}
                >
                  <div className="library-thumb-wrap">
                    <button type="button" className="library-menu-trigger" title="Image actions" aria-label={`Actions for ${promptDisplay}`} aria-haspopup="menu" disabled={!!bulkJob} onClick={event=>openMenu(event,img)}><MoreHorizontal size={18}/></button>
                    {selectionMode&&<label className="library-selection" onClick={event=>event.stopPropagation()}><input type="checkbox" aria-label={`Select ${promptDisplay}`} checked={selectedIds.includes(img.id)} disabled={!!bulkJob} onChange={()=>toggleSelection(img.id)}/></label>}
                    {img.fileExists===false&&!img.thumbnail?<FileImage size={42} aria-label="Original file missing"/>:<img
                      src={thumbSrc}
                      alt={promptDisplay}
                      loading="lazy"
                      className="library-thumb"
                    />}
                    <div className="library-thumb-overlay">
                      <button
                        type="button"
                        className="library-overlay-action"
                        title="View details & metadata"
                        onClick={(e) => {
                          e.stopPropagation();
                          setSelectedImage(img);
                        }}
                      >
                        <Eye size={16} />
                      </button>
                      <button
                        type="button"
                        className="library-overlay-action"
                        title="Inspect full resolution"
                        onClick={(e) => {
                          e.stopPropagation();
                          onOpenLightbox?.(imagePath, dims);
                        }}
                      >
                        <Maximize2 size={16} />
                      </button>
                    </div>
                  </div>

                  <div className="library-card-info">
                    <p className="library-card-prompt" title={promptDisplay}>
                      {promptDisplay}
                    </p>
                    <div className="library-card-meta">
                      {img.hidden&&<span>{img.fileExists===false?'File missing':'In Trash'}</span>}
                      <span>{dims || "Standard"}</span>
                      {isUpscaled && <span className="library-card-badge">Upscaled</span>}
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>

      {menu&&<LibraryImageMenu menu={menu} onClose={()=>setMenu(null)} onAction={imageAction}/>}
      {comparison&&<Dialog.Root open onOpenChange={open=>{if(!open)setComparison(null)}}><Dialog.Portal><Dialog.Backdrop className="library-compare-backdrop"/><Dialog.Popup className="library-compare-dialog"><Dialog.Title>Compare image versions</Dialog.Title><Dialog.Description>Original and edited files are shown independently. Missing files remain identified.</Dialog.Description><div className="image-compare">{['before','after'].map(side=>{const image=comparison.images.find(i=>i.id===comparison[side]);return <figure key={side}><label>{side==='before'?'Before':'After'}<select aria-label={`${side} version`} value={comparison[side]} onChange={e=>setComparison(old=>({...old,[side]:e.target.value}))}>{comparison.images.map(i=><option key={i.id} value={i.id}>{i.displayName||i.path.split(/[\\/]/).pop()} {i.hidden?'(Trash)':''}</option>)}</select></label>{image?.fileExists===false?<p>Original file missing</p>:<img src={fileUrl(image?.path)} alt={`${side} version`}/>}<figcaption>{image?.operation||'Original'} · {image?.finalDimensions}</figcaption></figure>;})}</div><Dialog.Close className="details-btn">Close comparison</Dialog.Close></Dialog.Popup></Dialog.Portal></Dialog.Root>}
      {selectedImage && (
        <ImageDetailsViewer
          image={selectedImage}
          onClose={() => setSelectedImage(null)}
          bridge={bridge}
          onSelectChat={onSelectChat}
          onPrefillPrompt={onPrefillPrompt}
          onOpenLightbox={onOpenLightbox}
          onDeleted={handleDeleted}
          onVariationStarted={onVariationStarted}
        />
      )}
    </div>
  );
}

function WelcomeSuggestions({ onSelectPrompt }) {
  const suggestions = [
    {
      icon: <WandSparkles size={16} />,
      title: "Generate an image",
      subtitle: "Cinematic 4K UHD with local FLUX.2 Klein",
      prompt: "Create a 3840x2160 cinematic image of a futuristic neon city in rain with reflective puddles"
    },
    {
      icon: <FileText size={16} />,
      title: "Create a document",
      subtitle: "Formatted PDF, Word, or presentation",
      prompt: "Create a PDF document summarizing quantum computing breakthroughs in 2026"
    },
    {
      icon: <Cpu size={16} />,
      title: "Code & Engineering",
      subtitle: "Write or debug scripts with GPT-OSS",
      prompt: "Write a high-performance Python script to process and validate large CSV data asynchronously"
    },
    {
      icon: <Layers size={16} />,
      title: "Local Document Analysis",
      subtitle: "Ask questions or edit local files",
      prompt: "Explain the main differences between classical and diffusion-based image generation"
    }
  ];

  return (
    <div className="welcome-suggestions-grid">
      {suggestions.map((item, i) => (
        <button key={i} type="button" className="welcome-suggestion-card" onClick={() => onSelectPrompt(item.prompt)}>
          <div className="suggestion-icon">{item.icon}</div>
          <div className="suggestion-text">
            <strong>{item.title}</strong>
            <small>{item.subtitle}</small>
          </div>
        </button>
      ))}
    </div>
  );
}

function markdownAstText(node) {
  if (typeof node?.value === "string") return node.value;
  return Array.isArray(node?.children) ? node.children.map(markdownAstText).join("") : "";
}

function reactText(children) {
  if (typeof children === "string" || typeof children === "number") return String(children);
  if (Array.isArray(children)) return children.map(reactText).join("");
  if (React.isValidElement(children)) return reactText(children.props.children);
  return "";
}

function unwrapOuterPromptEnvelope(text) {
  if (!text || typeof text !== "string") return text;
  const trimmed = text.trim();
  const fenceMatch = trimmed.match(/^(`{3,}|~{3,})([a-zA-Z0-9_-]*)\s*\n([\s\S]*?)\n\1$/);
  if (fenceMatch) {
    const lang = (fenceMatch[2] || "").toLowerCase();
    const body = fenceMatch[3];
    const hasInnerFences = /(`{3,}|~{3,})/.test(body);
    const hasNumberedTasks = /(?:^|\n)\s*(?:Task|Question|Part)?\s*\d+[.)]/i.test(body);
    const isDocLang = !lang || ["markdown", "md", "text", "txt"].includes(lang);
    if (hasInnerFences || hasNumberedTasks || isDocLang) {
      return body.trim();
    }
  }
  return text;
}

function renderInlineFormatting(text) {
  if (!text || typeof text !== "string") return text;
  const parts = [];
  const inlineRegex = /(`[^`\n]+`|\*\*[^*\n]+\*\*|\*[^*\n]+\*)/g;
  let lastIndex = 0;
  let match;
  let key = 0;

  while ((match = inlineRegex.exec(text)) !== null) {
    if (match.index > lastIndex) {
      parts.push(text.substring(lastIndex, match.index));
    }
    const token = match[0];
    if (token.startsWith("`") && token.endsWith("`")) {
      parts.push(<code key={key++} className="user-inline-code">{token.slice(1, -1)}</code>);
    } else if (token.startsWith("**") && token.endsWith("**")) {
      parts.push(<strong key={key++}>{token.slice(2, -2)}</strong>);
    } else if (token.startsWith("*") && token.endsWith("*")) {
      parts.push(<em key={key++}>{token.slice(1, -1)}</em>);
    }
    lastIndex = match.index + token.length;
  }
  if (lastIndex < text.length) {
    parts.push(text.substring(lastIndex));
  }
  return parts.length > 0 ? parts : text;
}

function parseUserMessageSegments(rawContent) {
  if (!rawContent || typeof rawContent !== "string") return [];
  const text = unwrapOuterPromptEnvelope(rawContent);
  const lines = text.split("\n");
  const segments = [];
  let currentText = [];
  let inFence = false;
  let fenceMarker = "";
  let fenceLang = "";
  let fenceCode = [];

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    const fenceStartMatch = !inFence && line.match(/^[ \t]*(`{3,}|~{3,})([a-zA-Z0-9_-]*)\s*$/);
    if (fenceStartMatch) {
      inFence = true;
      fenceMarker = fenceStartMatch[1];
      fenceLang = fenceStartMatch[2];
      fenceCode = [];
      if (currentText.length > 0) {
        segments.push({ type: "text", content: currentText.join("\n") });
        currentText = [];
      }
      continue;
    }

    if (inFence) {
      const fenceEndMatch = line.match(/^[ \t]*(`{3,}|~{3,})\s*$/);
      if (fenceEndMatch && fenceEndMatch[1][0] === fenceMarker[0] && fenceEndMatch[1].length >= fenceMarker.length) {
        inFence = false;
        segments.push({
          type: "code",
          language: fenceLang,
          content: fenceCode.join("\n")
        });
        fenceMarker = "";
        fenceLang = "";
        fenceCode = [];
        continue;
      }
      fenceCode.push(line);
      continue;
    }

    currentText.push(line);
  }

  if (inFence) {
    segments.push({
      type: "code",
      language: fenceLang,
      content: fenceCode.join("\n")
    });
  } else if (currentText.length > 0) {
    segments.push({ type: "text", content: currentText.join("\n") });
  }

  return segments;
}

function UserMessageRenderer({ content, bridge }) {
  const segments = useMemo(() => parseUserMessageSegments(content), [content]);

  return (
    <div className="user-message-body">
      {segments.map((seg, idx) => {
        if (seg.type === "code") {
          return (
            <CodeBlock key={idx} bridge={bridge}>
              <code className={seg.language ? `language-${seg.language}` : ""}>{seg.content}</code>
            </CodeBlock>
          );
        }
        return (
          <div key={idx} className="user-text-segment">
            {renderInlineFormatting(seg.content)}
          </div>
        );
      })}
    </div>
  );
}

function CodeBlock({ node, children, bridge, ...props }) {
  const [copied, setCopied] = useState(false);
  const [codeCheck,setCodeCheck]=useState(null);
  // Use the Markdown AST's text nodes, not React element stringification.
  // Highlight.js wraps tokens in nested spans; coercing those elements loses
  // indentation and can put UI labels into the clipboard. The toolbar remains
  // a sibling of <pre>, so it is never part of the code or message content.
  const source = markdownAstText(node) || reactText(children);
  const copy = async () => {
    try {
      if (await copyLocalText(bridge, source)) {
        setCopied(true);
        window.setTimeout(() => setCopied(false), 1400);
      }
    } catch {}
  };

  let lang = "";
  if (React.isValidElement(children) && children.props?.className) {
    const m = String(children.props.className).match(/language-([a-zA-Z0-9_-]+)/);
    if (m) lang = m[1];
  } else if (props.className) {
    const m = String(props.className).match(/language-([a-zA-Z0-9_-]+)/);
    if (m) lang = m[1];
  } else if (node?.children?.[0]?.properties?.className) {
    const classes = node.children[0].properties.className;
    const classStr = Array.isArray(classes) ? classes.join(" ") : String(classes);
    const m = classStr.match(/language-([a-zA-Z0-9_-]+)/);
    if (m) lang = m[1];
  }

  const langLabel = lang ? lang.toUpperCase() : "CODE";

  return (
    <div className="code-block">
      <div className="code-block-header">
        <span className="code-lang-tag">{langLabel}</span>
        {['python','py'].includes(lang.toLowerCase()) && <button className="code-copy-btn" onClick={async()=>{try{const r=await workspaceCall(bridge,'codeCheck',{code:source});setCodeCheck(r.value)}catch(e){setCodeCheck({error:e.message})}}}>Check syntax</button>}
        <button
          type="button"
          className="code-copy-btn"
          onClick={copy}
          aria-label={copied ? "Code copied" : "Copy code"}
          title={copied ? "Code copied" : "Copy code"}
        >
          {copied ? <Check size={13} className="text-success" aria-hidden="true" /> : <Copy size={13} aria-hidden="true" />}
          <span>{copied ? "Copied" : "Copy code"}</span>
        </button>
      </div>
      {codeCheck&&<div className="code-check-result" role="status">{codeCheck.valid?'Syntax passed · not executed':codeCheck.error}{codeCheck.warnings?.map(w=><p key={w}>{w}</p>)}</div>}
      <pre {...props}>{children}</pre>
    </div>
  );
}

function ActivityIndicator() {
  return <span className="activity-spinner" aria-hidden="true" />;
}

function AssistantActivity({ status, activity, elapsedSeconds }) {
  const count = activity?.expectedIds?.length || 0;
  const completed = activity?.completedIds?.length || 0;
  const active = activity?.activeId;
  const processing = active != null && !activity?.completedIds?.includes(active) ? 1 : 0;
  const remaining = Math.max(0, count - completed - processing);
  const phase = /load|starting|engine/i.test(status || "") ? "Loading model" : /reading|retriev|search/i.test(status || "") ? "Reading context" : /stop/i.test(status || "") ? "Stopping" : activity?.phase === "Generating answer" ? "Responding" : "Thinking";
  return <div className="assistant-activity" role="status">
    <div className="activity-heading"><ActivityIndicator /><span>{phase}…</span><time>{formatDuration(elapsedSeconds)}</time></div>
    <details className="activity-details"><summary>Activity details</summary><p>{status}</p>
      {activity?.multiTask && count > 1 && <div className="activity-counts"><span>{completed} completed</span><span>{processing} processing{active != null ? ` · Task ${active}` : ""}</span><span>{remaining} remaining</span></div>}
    </details>
  </div>;
}

const markdownPlugins = [remarkGfm];
const highlightPlugins = [rehypeHighlight];
const AssistantMarkdown = React.memo(function AssistantMarkdown({ content, bridge }) {
  const components = useMemo(() => ({
    pre: (props) => <CodeBlock {...props} bridge={bridge} />,
    a: ({node,href,children,...props}) => href?.startsWith('offlinegpt://chat/') ? <button className="citation-link" onClick={()=>{try{const u=new URL(href);bridge?.navigateMemorySource?.(u.pathname.replace(/^\//,''),Number(u.searchParams.get('message')))}catch{}}}>{children}</button> : <a href={href} {...props}>{children}</a>,
    table: ({ node, ...props }) => <div className="table-scroll" tabIndex={0} aria-label="Scrollable table"><table {...props} /></div>,
  }), [bridge]);
  return <ReactMarkdown urlTransform={url=>url.startsWith("offlinegpt://")?url:/^(https?:|mailto:|#)/.test(url)?url:""} remarkPlugins={markdownPlugins} rehypePlugins={highlightPlugins} components={components}>{content}</ReactMarkdown>;
});

const Message = React.memo(function Message({ message: sourceMessage, bridge, onOpenLightbox, elapsedSeconds, renderClock, canRegenerate, onPrefillPrompt }) {
  const [responseVersion, setResponseVersion] = useState(-1);
  const versions=sourceMessage.responseVersions || [];
  const versionIndex=responseVersion<0 ? versions.length-1 : Math.min(responseVersion,versions.length-1);
  const message=responseVersion>=0 && versions[versionIndex] ? {...sourceMessage,...versions[versionIndex],id:sourceMessage.id} : sourceMessage;
  const [copied, setCopied] = useState(false);
  const [bookmarked,setBookmarked]=useState(!!sourceMessage.bookmarked);
  const [bookmarkError,setBookmarkError]=useState("");
  useEffect(()=>setBookmarked(!!sourceMessage.bookmarked),[sourceMessage.bookmarked]);
  const [showRaw, setShowRaw] = useState(false);
  const [userShowRaw, setUserShowRaw] = useState(false);
  const [userCopied, setUserCopied] = useState(false);
  const [exportingPdf, setExportingPdf] = useState(false);
  const [showDetails, setShowDetails] = useState(false);
  const [speechPlayback, setSpeechPlayback] = useState({});
  const [speechError, setSpeechError] = useState("");
  const speechActiveRef=useRef(false);
  useEffect(() => {
    if (!bridge?.event) return;
    const receive=raw=>{const event=parse(raw,{});if(event.type!=="speechPlayback")return;
      const own=String(event.playback?.messageId)===String(sourceMessage.id);
      if(own || speechActiveRef.current){setSpeechPlayback(event.playback || {});if(own && event.playback?.error)setSpeechError(event.playback.error);}
      speechActiveRef.current=own;};
    bridge.event.connect(receive);
    return ()=>bridge.event.disconnect(receive);
  },[bridge,sourceMessage.id]);
  const readAloud = async () => {
    const state=parse(await call(bridge,"getSpeechStatus"),{});setSpeechPlayback(state);
    const result=parse(await call(bridge,"speakResponse",String(message.id),isImage ? `${message.imageAction==="edit_image" ? "Edited image" : message.imageAction==="enhance_image" ? "Enhanced image" : "Generated image"}. ${message.dimensions || ""}. ${message.notice || ""}` : stripUiControlArtifacts(message.content)),{});
    setSpeechError(result.ok ? "" : result.error || "Offline speech is unavailable.");
  };
  const [performanceMode, setPerformanceMode] = useState(() => {try{return localStorage.getItem("offline-gpt-performance")==="true";}catch{return false;}});
  const [localAttachment, setLocalAttachment] = useState("");
  const user = message.role === "user";
  const assistant = message.role === "assistant";
  const responseText = assistant ? stripUiControlArtifacts(message.content) : message.content;
  const isImage = message.type === "image" || !!message.images?.length || (assistant && message.attachment && /\.(png|jpe?g|webp|bmp)$/i.test(message.attachment));
  const isImageGenerating = assistant && !!message.status && (message.taskType === "image" || message.activeKind === "image" || message.activeKind === "image_prompt" || /Generating image|Improving the image prompt|Preparing the local image engine|Upscaling/i.test(message.status || ""));
  const effectiveAttachment = localAttachment || (assistant && message.outputMode === "CHAT" ? "" : message.attachment);
  const attachedFiles = user ? (message.attachments || (effectiveAttachment ? [effectiveAttachment] : [])) : (effectiveAttachment ? [effectiveAttachment] : []);
  const documentAttachment = assistant && effectiveAttachment && /\.(pdf|docx|pptx|txt|md|rtf)$/i.test(effectiveAttachment) ? effectiveAttachment : "";
  const isFailure = assistant && (message.error || /^(?:Error:|Image engine error:|Vision error:|Document error:|GPT-OSS prompt planning error:|Routing error:)/i.test(message.content || ""));
  useLayoutEffect(() => {
    if (!assistant || !responseText || !renderClock?.started || !bridge?.recordRendering) return;
    const first = !renderClock.firstAnswerReported && !renderClock.firstAnswerPending;
    const final = !message.streaming && !message.status && !renderClock.finalAnswerReported && !renderClock.finalAnswerPending;
    if (!first && !final) return;
    if (first) renderClock.firstAnswerPending = true;
    if (final) renderClock.finalAnswerPending = true;
    const commit = performance.now();
    let secondFrame;
    const frame = requestAnimationFrame(() => {
      secondFrame = requestAnimationFrame(() => {
        if (first) { renderClock.firstAnswerReported = true; renderClock.firstAnswerPending = false; }
        if (final) { renderClock.finalAnswerReported = true; renderClock.finalAnswerPending = false; }
        bridge.recordRendering(JSON.stringify({requestId: message.requestId || message.id,
          ...(first ? {firstAnswerVisibleMs: performance.now() - renderClock.started, firstBridgeToPaintMs: renderClock.firstContentReceived ? performance.now()-renderClock.firstContentReceived : null} : {}),
          ...(final ? {finalAnswerVisibleMs: performance.now() - renderClock.started} : {}),
          renderCommitMs: performance.now() - commit}));
      });
    });
    // Do not cancel a first-paint measurement on the next token commit.
    // This finite callback owns the immutable operation clock and request ID.
  }, [responseText, message.streaming, message.status, assistant, bridge, renderClock, message.id]);

  useLayoutEffect(() => {
    if (!assistant || !message.status || !renderClock?.started || renderClock.progressReported || renderClock.progressPending) return;
    renderClock.progressPending = true;
    let secondFrame;
    const frame = requestAnimationFrame(() => { secondFrame = requestAnimationFrame(() => {
      renderClock.progressReported = true; renderClock.progressPending = false;
      bridge?.recordRendering?.(JSON.stringify({requestId:message.requestId || message.id, firstProgressVisibleMs:performance.now()-renderClock.started}));
    }); });
  }, [assistant, message.status, bridge, renderClock, message.id]);

  const handleCopy = async () => {
    if (!responseText) return;
    try {
      if (!await copyLocalText(bridge, responseText)) return;
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {}
  };

  const handleUserCopy = async () => {
    if (!message.content) return;
    try {
      if (await copyLocalText(bridge, message.content)) {
        setUserCopied(true);
        window.setTimeout(() => setUserCopied(false), 1500);
      }
    } catch {}
  };

  const handleExportPdf = async () => {
    if (!responseText || exportingPdf) return;
    setExportingPdf(true);
    try {
      const raw = await call(bridge, "exportResponsePdf", responseText);
      const res = parse(raw, {});
      if (res?.ok && res.path) {
        setLocalAttachment(res.path);
      }
    } catch {}
    finally {
      setExportingPdf(false);
    }
  };

  const tokenCount = Number(message.tokenCount || message.tokens) || (responseText ? Math.round(responseText.length / 4) : 0);
  const speedText = message.speed ? (typeof message.speed === "number" ? `${message.speed.toFixed(1)} tok/s` : String(message.speed).includes("tok/s") ? message.speed : `${message.speed} tok/s`) : null;

  return <article data-source-message={message.sourceMessageId} className={"message-row " + (message.enterAnimate ? "message-enter " : "") + (user ? "user-message" : "assistant-message")}>
    <div className="message-content">
      {user && <div className="message-bubble user-bubble">
        {message.content && (
          userShowRaw
            ? <pre className="raw-response-text user-raw-text">{message.content}</pre>
            : <UserMessageRenderer content={message.content} bridge={bridge} />
        )}
        {attachedFiles.map((path) => <MessageAttachment key={path.path || path} value={path} bridge={bridge} />)}
      </div>}
      {user && message.content && <div className="message-actions user-message-actions">
        <button onClick={handleUserCopy} title={userCopied ? "Copied!" : "Copy message"}>
          {userCopied ? <Check size={14} /> : <Clipboard size={14} />}
        </button>
        <button className="action-text-button" onClick={() => setUserShowRaw(!userShowRaw)} title={userShowRaw ? "Show formatted message" : "Show raw message"}>
          <span>{userShowRaw ? "Formatted" : "Raw"}</span>
        </button>
      </div>}
      {assistant && isImageGenerating && <ImageGenerationProgress status={message.status} prompt={message.prompt} userPrompt={message.userPrompt} promptPrepared={message.promptPrepared} imageAction={message.imageAction} dimensions={message.targetDimensions || message.dimensions} progress={message.imageProgress} bridge={bridge} elapsedSeconds={elapsedSeconds} />}
      {assistant && message.status && !isImageGenerating && <AssistantActivity status={message.status} activity={message.activity} elapsedSeconds={elapsedSeconds} />}
      {assistant && isFailure && <div className="task-error-card" role="alert"><AlertCircle size={17} /><div><strong>Task could not finish</strong><p>{message.content}</p>{canRegenerate && <button type="button" onClick={() => {setResponseVersion(-1);bridge?.retryLastMessage?.();}}><RotateCcw size={13} /> Retry</button>}</div></div>}
      {assistant && !isFailure && responseText && (!isImage || documentAttachment) && (showRaw
        ? <pre className="raw-response-text">{stripUiControlArtifacts(message.raw_content || responseText)}</pre>
        : <div className={`markdown assistant-markdown ${message.streaming ? "streaming-active" : ""}`}><AssistantMarkdown content={responseText} bridge={bridge} />{message.streaming && <span className="streaming-cursor" aria-hidden="true" />}</div>)}
      {assistant && isImage && <ImageResultCard message={message} bridge={bridge} onOpenLightbox={onOpenLightbox} canRegenerate={canRegenerate} onPrefillPrompt={onPrefillPrompt} />}
      {documentAttachment && <div className="document-result"><FileText size={18} /><div><strong>{documentAttachment.split(/[\\/]/).pop()}</strong><small>Saved locally · original attachments stay unchanged</small></div><button className="text-action" onClick={() => bridge?.openFile?.(documentAttachment)}>Open <ArrowUpRight size={14} /></button></div>}
      {assistant && !isImage && responseText && <div className="message-actions">
        <button onClick={handleCopy} title={copied ? "Copied!" : "Copy response"}>
          {copied ? <Check size={14} /> : <Clipboard size={14} />}
        </button>
        {sourceMessage.sourceMessageId!=null&&<button type="button" title={bookmarked?'Remove bookmark':'Bookmark response'} aria-label={bookmarked?'Remove bookmark':'Bookmark response'} aria-pressed={bookmarked} onClick={async()=>{const result=parse(await call(bridge,'bookmarkMessage',String(sourceMessage.sourceMessageId),!bookmarked),{});if(result.ok){setBookmarked(result.bookmarked);setBookmarkError('');}else setBookmarkError(result.error||'Bookmark could not be saved.');}}><Bookmark size={14}/></button>}
        {bookmarkError&&<span role="alert">{bookmarkError}</span>}
        {canRegenerate && <button type="button" title="Regenerate latest response" aria-label="Regenerate latest response" onClick={() => {setResponseVersion(-1);bridge?.retryLastMessage?.();}}><RotateCcw size={14}/></button>}
        <button type="button" title="Read aloud offline" aria-label="Read aloud offline" onClick={readAloud}><Volume2 size={15}/></button>
        <button type="button" title="Response details" aria-label="Response details" aria-expanded={showDetails} onClick={() => setShowDetails(!showDetails)}><MoreHorizontal size={15}/></button>
        <div className="message-meta-info">
          {performanceMode && tokenCount > 0 && <span className="meta-chip">~{tokenCount} visible tokens</span>}
          {performanceMode && speedText && <span className="meta-chip">{speedText} decode</span>}
          {message.duration != null && Number.isFinite(Number(message.duration)) ? <span>Worked for {formatDuration(Number(message.duration))}</span> : null}
        </div>
      </div>}
      {assistant && versions.length > 1 && <div className="response-versions" aria-label="Response versions"><button aria-label="Previous response version" disabled={versionIndex<=0} onClick={()=>setResponseVersion(versionIndex-1)}><ChevronRight size={13} style={{transform:"rotate(180deg)"}}/></button><span>{versionIndex+1} / {versions.length}</span><button aria-label="Next response version" disabled={versionIndex>=versions.length-1} onClick={()=>setResponseVersion(versionIndex+1)}><ChevronRight size={13}/></button></div>}
      {assistant && message.metrics?.memory_sources?.length>0 && <details className="memory-sources"><summary>Local conversation sources ({message.metrics.memory_sources.length})</summary><small>{message.metrics.memory_coverage?.coverageComplete?'Retrieved set complete':'Selected evidence; history coverage may be partial'}</small>{message.metrics.memory_sources.slice(0,30).map(source=><button key={`${source.id}-${source.chatId}`} onClick={()=>bridge?.navigateMemorySource?.(source.chatId,source.messageId)}>{source.id} · {source.title} · {source.date} · {source.evidenceKind}</button>)}</details>}
      {assistant && speechError && <p className="speech-error" role="alert">{speechError}</p>}
      {assistant && String(speechPlayback.messageId) === String(message.id) && ["loading","speaking","paused","error"].includes(speechPlayback.state) && <section className="speech-player" aria-label="Offline voice playback">
        <span><Volume2 size={14}/>{speechPlayback.state==="loading" ? "Preparing voice…" : "Reading aloud"}</span>
        {speechPlayback.pauseAvailable && <IconButton title={speechPlayback.state === "paused" ? "Resume speech" : "Pause speech"} onClick={() => bridge?.controlSpeech?.(speechPlayback.state === "paused" ? "resume" : "pause")} >{speechPlayback.state === "paused" ? <Play size={14}/> : <Pause size={14}/>}</IconButton>}
        <IconButton title="Replay speech" onClick={()=>bridge?.controlSpeech?.("replay")}><RotateCcw size={14}/></IconButton>
        <IconButton title="Stop speech" onClick={() => bridge?.controlSpeech?.("stop")}><Square size={13}/></IconButton>
        {speechPlayback.progressAvailable && <progress aria-label="Spoken text progress" value={speechPlayback.position || 0} max={speechPlayback.length || 1}/>}
        <select aria-label="Offline voice" value={speechPlayback.voice || ""} onChange={e=>bridge?.speechSettings?.(JSON.stringify({voice:e.target.value}))}>{(speechPlayback.voiceOptions || []).map(v=><option key={v.id} value={v.id}>{v.label}</option>)}</select>
        <label>Speed <select aria-label="Speaking speed" value={String(speechPlayback.speed || 1)} onChange={e=>bridge?.speechSettings?.(JSON.stringify({speed:Number(e.target.value)}))}>{[.75,1,1.25,1.5,1.75,2].map(v=><option key={v} value={String(v)}>{v}×</option>)}</select></label>
      </section>}
      {assistant && !isImage && (showDetails || performanceMode) && <section className="response-details" aria-label="Response details">
        <div className="details-actions"><button type="button" onClick={() => setShowRaw(!showRaw)}>{showRaw ? "Formatted output" : "Raw output"}</button><button type="button" onClick={handleExportPdf} disabled={exportingPdf}>{exportingPdf ? "Exporting…" : "Export PDF"}</button>
        <label><input type="checkbox" checked={performanceMode} onChange={e => {setPerformanceMode(e.target.checked);try{localStorage.setItem("offline-gpt-performance",String(e.target.checked));}catch{}}}/> Performance mode</label></div>
        <dl>{[["PDF parse",message.metrics?.pdf_parse_ms != null ? `${message.metrics.pdf_parse_ms} ms` : null],["PDF index",message.metrics?.pdf_index_ms != null ? `${message.metrics.pdf_index_ms} ms` : null],["Retrieval",message.metrics?.retrieval_ms != null ? `${message.metrics.retrieval_ms} ms` : null],["Total time",message.duration != null ? `${Number(message.duration).toFixed(2)} s` : null],["Model PID",message.metrics?.backend_pid],["Resident model reused",message.metrics?.model_reused == null ? null : message.metrics.model_reused ? "Yes" : "No"],["Reload reason",message.metrics?.reload_reason],["Bridge to paint",message.metrics?.frontend?.firstBridgeToPaintMs == null ? null : `${Number(message.metrics.frontend.firstBridgeToPaintMs).toFixed(1)} ms`],["Model load",message.metrics?.model_load_ms != null ? `${message.metrics.model_load_ms} ms` : message.model_load_ms != null ? `${message.model_load_ms} ms` : null],["Prompt tokens",message.metrics?.prompt_tokens ?? message.prompt_tokens],["Prompt prefill",message.metrics?.prefill_ms != null ? `${message.metrics.prefill_ms} ms` : null],["Decode speed",speedText ? `${speedText} (includes reasoning)` : null],["Decoded tokens",message.metrics?.runtime_generated_tokens],["Decode time",message.metrics?.decode_ms != null ? `${message.metrics.decode_ms} ms` : null],["Cached prompt tokens",message.metrics?.cached_tokens],["First answer visible",message.metrics?.frontend?.firstAnswerVisibleMs != null ? `${Number(message.metrics.frontend.firstAnswerVisibleMs).toFixed(1)} ms` : null],["Render commit",message.metrics?.frontend?.renderCommitMs != null ? `${Number(message.metrics.frontend.renderCommitMs).toFixed(1)} ms` : null],["Tool calls",message.metrics?.tool_calls],["Model calls",message.metrics?.model_calls ?? message.model_calls],["Retries",message.metrics?.retries ?? message.retries]].filter(([,v])=>v!=null).map(([k,v])=><div key={k}><dt>{k}</dt><dd>{v}</dd></div>)}</dl>
      </section>}
      {assistant && isImage && (
        <div className="message-actions image-meta-actions"><button type="button" title="Read aloud offline" aria-label="Read aloud offline" onClick={readAloud}><Volume2 size={15}/></button>
          <div className="message-meta-info">
            {message.duration != null && Number.isFinite(Number(message.duration)) ? <span>Worked for {formatDuration(Number(message.duration))}</span> : null}
          </div>
        </div>
      )}
      {assistant && isImage && message.metrics?.image_performance && <details className="response-details"><summary>Image performance</summary><dl>{Object.entries(message.metrics.image_performance.stages||{}).filter(([,v])=>v!=null).map(([k,v])=><div key={k}><dt>{k.toLowerCase().replaceAll('_',' ')}</dt><dd>{Number(v).toLocaleString()} {k.endsWith('_MS')?'ms':''}</dd></div>)}</dl><small>Stage times can overlap. Unavailable backend measurements are omitted.</small></details>}
    </div>
  </article>;
});

function formatDuration(seconds) {
  const safe = Math.max(0, Math.floor(Number(seconds) || 0));
  const hours = Math.floor(safe / 3600);
  const minutes = Math.floor((safe % 3600) / 60);
  const remainder = safe % 60;
  if (hours) return `${hours}h ${String(minutes).padStart(2, "0")}m ${String(remainder).padStart(2, "0")}sec`;
  if (minutes) return `${minutes}m ${String(remainder).padStart(2, "0")}sec`;
  return `${remainder}sec`;
}

function stripUiControlArtifacts(value) {
  const lines = String(value || "").match(/[^\n]*\n|[^\n]+$/g) || [];
  let fenceChar = "";
  let fenceSize = 0;
  let hasVisibleContent = false;
  return lines.filter((line) => {
    const body = line.replace(/[\r\n]+$/, "");
    const artifact = body.trim().toLowerCase();
    if (!fenceChar && (artifact === "svgcopy" || (hasVisibleContent && ["svg", "copy"].includes(artifact)))) return false;
    const fence = body.match(/^[ \t]*(`{3,}|~{3,})/);
    if (fence) {
      const marker = fence[1];
      if (!fenceChar) { fenceChar = marker[0]; fenceSize = marker.length; }
      else if (marker[0] === fenceChar && marker.length >= fenceSize) { fenceChar = ""; fenceSize = 0; }
    }
    if (body.trim()) hasVisibleContent = true;
    return true;
  }).join("");
}

function applyCompletedDuration(items, duration) {
  if (duration == null || !Number.isFinite(Number(duration))) return items;
  let index = -1;
  for (let i = items.length - 1; i >= 0; i -= 1) {
    if (items[i].role === "assistant") { index = i; break; }
  }
  if (index < 0) return items;
  const next = [...items];
  if (next[index].duration != null) return items;
  next[index] = { ...next[index], duration: Number(duration) };
  return next;
}

function convertHtmlToMarkdown(html, fallbackText = "") {
  if (!html || typeof html !== "string") return fallbackText;
  try {
    const parser = new DOMParser();
    const doc = parser.parseFromString(html, "text/html");
    if (!doc?.body) return fallbackText;

    const renderNode = (node, listContext = null) => {
      if (!node) return "";
      if (node.nodeType === Node.TEXT_NODE) return node.nodeValue || "";
      if (node.nodeType !== Node.ELEMENT_NODE) return "";

      const tag = node.tagName.toLowerCase();

      // Handle Code Blocks (<pre>)
      if (tag === "pre") {
        const codeElem = node.querySelector("code");
        const rawCodeText = (codeElem || node).textContent || "";
        const normalized = rawCodeText.replace(/\r\n/g, "\n").replace(/\r/g, "\n").replace(/\n+$/, "");
        const langMatch = (codeElem?.className || "").match(/language-([a-zA-Z0-9_-]+)/);
        const lang = (langMatch ? langMatch[1] : "").toLowerCase();

        // If this <pre> is actually an outer container holding markdown or multi-task text
        const hasInnerFences = /(`{3,}|~{3,})/.test(normalized);
        const hasNumberedTasks = /(?:^|\n)\s*(?:Task|Question|Part)?\s*\d+[.)]/i.test(normalized);
        const isDocLang = !lang || ["markdown", "md", "text", "txt"].includes(lang);

        if ((hasInnerFences || hasNumberedTasks) && isDocLang) {
          return `\n${normalized}\n\n`;
        }

        return `\n\`\`\`${lang}\n${normalized}\n\`\`\`\n\n`;
      }

      // Handle Inline Code (<code> outside <pre>)
      if (tag === "code") {
        const text = node.textContent || "";
        return `\`${text}\``;
      }

      // Handle Lists (<ol>, <ul>)
      if (tag === "ol" || tag === "ul") {
        const isOrdered = tag === "ol";
        const startAttr = parseInt(node.getAttribute("start"), 10);
        let counter = !isNaN(startAttr) ? startAttr : 1;
        const depth = listContext ? listContext.depth + 1 : 0;
        const indent = "   ".repeat(depth);

        let listText = "";
        const children = Array.from(node.childNodes);
        for (const child of children) {
          if (child.nodeType === Node.ELEMENT_NODE && child.tagName.toLowerCase() === "li") {
            const childContent = renderNode(child, { isOrdered, counter, depth, indent });
            listText += childContent;
            counter += 1;
          } else {
            listText += renderNode(child, listContext);
          }
        }
        return listText ? `\n${listText.trimEnd()}\n\n` : "";
      }

      // Handle List Items (<li>)
      if (tag === "li") {
        const depth = listContext ? listContext.depth : 0;
        const indent = "   ".repeat(depth);
        let prefix = "- ";
        if (listContext?.isOrdered) {
          prefix = `${listContext.counter}. `;
        }

        let itemContent = "";
        for (const child of Array.from(node.childNodes)) {
          itemContent += renderNode(child, listContext);
        }
        itemContent = itemContent.trim();

        if (/^\d+[\.\)]\s+/.test(itemContent) || /^[A-Za-z][\.\)]\s+/.test(itemContent)) {
          prefix = "";
        }

        return `${indent}${prefix}${itemContent}\n`;
      }

      // Handle Headings (<h1> - <h6>)
      if (/^h[1-6]$/.test(tag)) {
        const level = parseInt(tag[1], 10);
        const prefix = "#".repeat(level) + " ";
        let content = "";
        for (const child of Array.from(node.childNodes)) content += renderNode(child, listContext);
        return `\n${prefix}${content.trim()}\n\n`;
      }

      // Handle Paragraphs (<p>) and <div>
      if (tag === "p" || tag === "div") {
        let content = "";
        for (const child of Array.from(node.childNodes)) content += renderNode(child, listContext);
        return content.trim() ? `${content.trim()}\n\n` : "\n";
      }

      if (tag === "br") return "\n";

      if (tag === "blockquote") {
        let content = "";
        for (const child of Array.from(node.childNodes)) content += renderNode(child, listContext);
        return content.trim().split("\n").map((line) => `> ${line}`).join("\n") + "\n\n";
      }

      if (tag === "strong" || tag === "b") {
        let content = "";
        for (const child of Array.from(node.childNodes)) content += renderNode(child, listContext);
        return `**${content.trim()}**`;
      }
      if (tag === "em" || tag === "i") {
        let content = "";
        for (const child of Array.from(node.childNodes)) content += renderNode(child, listContext);
        return `*${content.trim()}*`;
      }

      let content = "";
      for (const child of Array.from(node.childNodes)) content += renderNode(child, listContext);
      return content;
    };

    let result = renderNode(doc.body).trim();
    result = result.replace(/\n{3,}/g, "\n\n");
    return result || fallbackText;
  } catch (e) {
    return fallbackText;
  }
}

function Composer({ bridge, chatId, busy, onSend, attachment, setAttachment, voiceState, voicePartial, voiceEvent, voiceError, clearVoiceError, onVoiceInserted, onVoiceToggle, dragActive = false, className = "", initialText = "", onPrefillConsumed }) {
  const [text, setText] = useState("");
  const [attachMenuOpen, setAttachMenuOpen] = useState(false);
  const draft=useRef({text:"",attachments:attachment});draft.current={text,attachments:attachment};
  const [draftReady,setDraftReady]=useState(false);const draftDirty=useRef(false);
  useEffect(()=>{
    if(!bridge)return;
    let live=true;
    call(bridge,'getPromptDraft',chatId||'').then(raw=>{if(!live)return;const value=parse(raw,{});if(!draftDirty.current&&!initialText){setText(value.text||'');if(!draft.current.attachments?.length)setAttachment(value.attachments||[]);}setDraftReady(true);});
    return()=>{live=false;if(draftDirty.current)bridge?.savePromptDraft?.(chatId||'',JSON.stringify(draft.current));};
  },[bridge,chatId]);
  useEffect(()=>{
    if(!bridge)return;
    const flush=async()=>{try{const value=parse(await call(bridge,'savePromptDraft',chatId||'',JSON.stringify(draft.current)),{});bridge.draftFlushFinished?.(!!value.ok);}catch{bridge.draftFlushFinished?.(false);}};
    window.offlineFlushDraft=flush;
    return()=>{if(window.offlineFlushDraft===flush)delete window.offlineFlushDraft;};
  },[bridge,chatId]);
  useEffect(()=>{if(!draftReady)return;draftDirty.current=true;const timer=setTimeout(()=>bridge?.savePromptDraft?.(chatId||'',JSON.stringify(draft.current)),250);return()=>clearTimeout(timer);},[text,attachment,draftReady,bridge,chatId]);
  const [transcriptOptions, setTranscriptOptions] = useState([]);
  const input = useRef(null);
  const voiceRange = useRef(null);
  const seenVoiceEvent = useRef(0);
  const voiceActive = ["loading", "listening", "transcribing"].includes(voiceState);
  useEffect(() => {
    if (!initialText) return;
    setText(initialText);
    onPrefillConsumed?.();
    requestAnimationFrame(() => { if (input.current) { input.current.style.height = "auto"; input.current.style.height = `${Math.min(input.current.scrollHeight, 200)}px`; input.current.focus(); } });
  }, [initialText, onPrefillConsumed]);
  useEffect(() => {
    if (typeof voicePartial !== "string") return;
    setText((value) => {
      const range = voiceRange.current;
      if (range?.partial) {
        const next = value.slice(0, range.start) + voicePartial + value.slice(range.end);
        voiceRange.current = { start: range.start, end: range.start + voicePartial.length, partial: true };
        return next;
      }
      if (!voicePartial) return value;
      const spacer = value && !value.endsWith(" ") ? " " : "";
      const start = value.length + spacer.length;
      const next = `${value}${spacer}${voicePartial}`;
      voiceRange.current = { start, end: next.length, partial: true };
      return next;
    });
    requestAnimationFrame(() => { if (input.current) { input.current.style.height = "auto"; input.current.style.height = `${Math.min(input.current.scrollHeight, 200)}px`; } });
  }, [voicePartial]);
  useEffect(() => {
    if (!voiceEvent || voiceEvent.id === seenVoiceEvent.current) return;
    seenVoiceEvent.current = voiceEvent.id;
    setText((value) => {
      const range = voiceRange.current;
      if (range) {
        const next = value.slice(0, range.start) + voiceEvent.text + value.slice(range.end);
        voiceRange.current = { start: range.start, end: range.start + voiceEvent.text.length, partial: false };
        return next;
      }
      const spacer = value && !value.endsWith(" ") ? " " : "";
      const next = `${value}${spacer}${voiceEvent.text}`;
      voiceRange.current = { start: value.length + spacer.length, end: next.length, partial: false };
      return next;
    });
    setTranscriptOptions((voiceEvent.alternatives || []).filter((option) => option && option !== voiceEvent.text).slice(0, 4));
    requestAnimationFrame(() => { if (input.current) { input.current.style.height = "auto"; input.current.style.height = `${Math.min(input.current.scrollHeight, 200)}px`; input.current.focus(); } });
    const timing=voiceEvent;
    requestAnimationFrame(()=>requestAnimationFrame(()=>bridge?.recordVoiceTiming?.(JSON.stringify({turnId:timing.turnId,stages:{BACKEND_TO_FRONTEND_MS:timing.receivedEpochMs-timing.emittedEpochMs,TEXT_VISIBLE_MS:Date.now()-timing.emittedEpochMs,TOTAL_POST_SPEECH_LATENCY_MS:Date.now()-timing.releasedEpochMs}}))));
    onVoiceInserted?.(voiceEvent.id);
  }, [voiceEvent, onVoiceInserted]);
  const chooseTranscript = (option) => {
    const range = voiceRange.current;
    if (!range) return;
    setText((value) => {
      const next = value.slice(0, range.start) + option + value.slice(range.end);
      voiceRange.current = { start: range.start, end: range.start + option.length };
      return next;
    });
    setTranscriptOptions([]);
    requestAnimationFrame(() => { if (input.current) { input.current.style.height = "auto"; input.current.style.height = `${Math.min(input.current.scrollHeight, 200)}px`; } });
  };
  const send = () => {
    if (busy) { bridge?.stop?.(); return; }
    const value = text.trim();
    if (!value) return;
    draft.current={text:"",attachments:[]};draftDirty.current=false;
    bridge?.savePromptDraft?.(chatId||'',JSON.stringify(draft.current));
    onSend(value, attachment);
    setText("");
    setAttachment([]);
    voiceRange.current = null;
    setTranscriptOptions([]);
    if (input.current) input.current.style.height = "auto";
  };
  const chooseFile = async (kind = "all") => {
    setAttachMenuOpen(false);
    const raw = await call(bridge, "pickAttachment", kind);
    const selected = typeof raw === "string" ? parse(raw, []) : raw;
    if (Array.isArray(selected)) setAttachment((files) => [...files, ...selected.filter((file) => file?.path && !files.some((old) => old.path === file.path))]);
    else if (selected?.path) setAttachment((files) => [...files, selected]);
  };
  const resize = (event) => { event.target.style.height = "auto"; event.target.style.height = String(Math.min(event.target.scrollHeight, 200)) + "px"; };

  const handlePaste = (e) => {
    const clipboardData = e.clipboardData || window.clipboardData;
    if (!clipboardData) return;
    const html = clipboardData.getData("text/html");
    const plainText = clipboardData.getData("text/plain");

    let processedText = null;
    if (html && /<(?:ol|ul|li|pre|code|table|h[1-6]|p|blockquote)\b/i.test(html)) {
      try {
        processedText = convertHtmlToMarkdown(html, plainText);
      } catch (err) {
        processedText = plainText;
      }
    }

    let textToInsert = (processedText != null && processedText.trim()) ? processedText : plainText;
    if (textToInsert != null) {
      textToInsert = unwrapOuterPromptEnvelope(textToInsert);
      e.preventDefault();
      const textarea = input.current;
      if (textarea) {
        const start = textarea.selectionStart ?? 0;
        const end = textarea.selectionEnd ?? 0;
        const currentVal = textarea.value;
        const nextVal = currentVal.substring(0, start) + textToInsert + currentVal.substring(end);
        setText(nextVal);
        requestAnimationFrame(() => {
          if (textarea) {
            const newPos = start + textToInsert.length;
            textarea.selectionStart = newPos;
            textarea.selectionEnd = newPos;
            resize({ target: textarea });
          }
        });
      } else {
        setText((prev) => prev + textToInsert);
      }
    }
  };

  return <div className={`composer-wrap ${className}`}>
    <div className="composer-shell">
      {dragActive && <div className="drop-overlay"><span><Plus size={17} /> Drop files to attach</span></div>}
      {attachment.length > 0 && <div className="attachment-strip">{attachment.map((file) => <FilePill key={file.path} file={file} onRemove={() => setAttachment((files) => files.filter((old) => old.path !== file.path))} />)}</div>}
      <textarea ref={input} value={text} onChange={(e) => { clearVoiceError?.(); if (!voiceActive) voiceRange.current = null; setTranscriptOptions([]); draftDirty.current=true;setText(e.target.value); resize(e); }} onPaste={handlePaste} onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } else if (e.key === "Tab") { e.preventDefault(); const textarea = input.current; if (textarea) { const start = textarea.selectionStart ?? 0; const end = textarea.selectionEnd ?? 0; const nextVal = text.substring(0, start) + "    " + text.substring(end); setText(nextVal); requestAnimationFrame(() => { textarea.selectionStart = textarea.selectionEnd = start + 4; }); } } }} placeholder={attachment.length ? "Tell Offline GPT what to do with this file…" : "Message Offline GPT"} rows={1} aria-label="Message input" />
      <div className="composer-toolbar">
        <div className="composer-tools">
          <div className="attach-menu-wrap">
            <IconButton title="Add files or create an image" aria-expanded={attachMenuOpen} onClick={() => setAttachMenuOpen((open) => !open)}><Plus size={19} /></IconButton>
            {attachMenuOpen && <><button className="menu-dismiss" aria-label="Close attachment menu" onClick={() => setAttachMenuOpen(false)} /><div className="attach-menu" role="menu">
              <button role="menuitem" onClick={() => chooseFile("image")}><FileImage size={16} /> Upload image</button>
              <button role="menuitem" onClick={() => chooseFile("document")}><FileText size={16} /> Upload document</button>
              <button role="menuitem" onClick={() => chooseFile("all")}><File size={16} /> Upload file</button>
              <div className="attach-menu-rule" />
              <button role="menuitem" onClick={() => { setAttachMenuOpen(false); setText((value) => value || "Create an image of "); input.current?.focus(); }}><ImagePlus size={16} /> Create image</button>
            </div></>}
          </div>
          <span className="privacy-note"><ShieldCheck size={13} /><span className="tool-label-text">Stored on this PC</span></span>
        </div>
        <div className="composer-actions">
          <IconButton title={voiceState === "transcribing" ? "Transcribing locally…" : voiceState === "loading" ? "Loading Whisper…" : voiceActive ? "Stop and transcribe" : "Dictate locally with Whisper"} className={voiceState === "listening" ? "voice-active" : "voice-button"} disabled={(busy && !voiceActive) || voiceState === "transcribing"} onClick={() => { clearVoiceError?.(); onVoiceToggle?.(); }}>{voiceState === "listening" ? <MicOff size={18} /> : <Mic size={18} />}</IconButton>
        <button className={"send-button " + (busy ? "stop-button" : "")} onClick={send} aria-label={busy ? "Stop generation" : "Send message"} disabled={!busy && !text.trim()} title={busy ? "Stop current task" : "Send"}>
          {busy ? <Square size={15} fill="currentColor" /> : <ArrowUp size={18} />}
        </button>
        </div>
      </div>
    </div>
    {voiceActive && <div className="voice-hint" aria-live="polite"><span className="listening-mark" /> {voiceState === "loading" ? "Loading the local Whisper model…" : voiceState === "transcribing" ? "Transcribing locally…" : "Listening locally · select the microphone to finish"} <button type="button" onClick={()=>bridge?.cancelVoiceInput?.()}>Cancel</button></div>}
    {voiceState === "completed" && <div className="voice-hint" aria-live="polite">Transcription added to the message field.</div>}
    {transcriptOptions.length > 0 && <div className="voice-alternatives"><span>Other recognized phrases</span>{transcriptOptions.map((option) => <button key={option} type="button" onClick={() => chooseTranscript(option)}>{option}</button>)}</div>}
    {voiceError && <div className="voice-error">{voiceError}</div>}
    <div className="composer-disclaimer">Offline GPT can make mistakes. Review important information and document edits before using them.</div>
  </div>;
}

const PROMPT_MODE_OPTIONS = [
  ["creative", "Creative Direction · Cinematic scene composition & professional photography (Recommended)"],
  ["smart_enhance", "Smart Enhance · Polish details, lighting, and textures"],
  ["original", "Original Prompt · Direct generation without expansion"]
];
const QUALITY_PRESET_OPTIONS = [
  ["balanced", "Balanced · 20 steps, crisp fidelity (Default)"],
  ["quality", "Quality · 28 steps, high detail"],
  ["fast", "Fast · 14 steps, rapid draft"]
];
const IMAGE_FORMAT_OPTIONS = [
  ["png", "PNG · lossless"],
  ["jpeg", "JPEG · smaller file"]
];
const CHAT_BACKEND_OPTIONS = [
  ["bundled", "Offline GPT · built in"],
  ["lmstudio", "LM Studio · optional external engine"]
];
const LIFECYCLE_OPTIONS = [
  ["balanced", "Balanced · keep loaded 90s idle (Recommended)"],
  ["low_ram", "Low RAM · unload immediately after response"],
  ["fast", "Fast · keep model resident across chats"]
];
const BACKEND_OPTIONS = [
  ["auto", "Automatic · detected GPU"],
  ["cuda0", "NVIDIA CUDA · GPU 0"],
  ["vulkan0", "Vulkan GPU"],
  ["cpu", "CPU · slow fallback"]
];
const MEMORY_PROFILE_OPTIONS = [
  ["low", "Memory safe · disk backed"],
  ["balanced", "Balanced · GPU + RAM"],
  ["performance", "Fast · more GPU memory"]
];
const LANGUAGE_OPTIONS = [
  ["auto", "Automatic · English default"],
  ["en", "English"],
  ["hi", "Hindi"],
  ["mr", "Marathi"],
  ["es", "Spanish"],
  ["fr", "French"],
  ["de", "German"],
  ["it", "Italian"],
  ["pt", "Portuguese"],
  ["ja", "Japanese"],
  ["ko", "Korean"],
  ["zh", "Chinese"]
];
const VAD_OPTIONS = [
  ["low", "Low · detect quieter speech"],
  ["medium", "Balanced"],
  ["high", "High · reduce background noise"]
];
const TRANSCRIPTION_MODE_OPTIONS = [
  ["streaming", "Final transcript · current local server"],
  ["final", "Final sentence only"]
];

const SettingField = memo(function SettingField({
  label,
  name,
  hint,
  type = "text",
  browseKind = null,
  options = null,
  disabled = false,
  value,
  onChange,
  onBrowse,
  onInspect = null,
  inspection = null
}) {
  const currentValue = options ? (value !== undefined ? value : options[0]?.[0] ?? "") : (value ?? "");
  return (
    <label className="setting-field">
      <span className="field-label">{label}</span>
      {options ? (
        <select
          disabled={disabled}
          value={currentValue}
          onChange={(e) => onChange?.(name, e.target.value)}
        >
          {options.map(([v, l]) => (
            <option key={v} value={v}>
              {l}
            </option>
          ))}
        </select>
      ) : (
        <div className="setting-input-row">
          <input
            disabled={disabled}
            type={type}
            value={currentValue}
            onChange={(e) =>
              onChange?.(name, type === "number" ? Number(e.target.value) : e.target.value)
            }
          />
          {browseKind && (
            <button
              type="button"
              className="browse-button"
              onClick={(e) => {
                e.preventDefault();
                onBrowse?.(name, browseKind);
              }}
            >
              {browseKind === "directory" ? "Choose folder" : "Browse"}
            </button>
          )}
          {onInspect && currentValue && (
            <button
              type="button"
              className="browse-button"
              title="Inspect GGUF metadata"
              onClick={(e) => {
                e.preventDefault();
                onInspect?.(name, currentValue);
              }}
            >
              Inspect
            </button>
          )}
        </div>
      )}
      {inspection && (
        <div className="model-inspection-badge">
          {inspection.error ? (
            <span className="inspection-warning">⚠ {inspection.error}</span>
          ) : (
            <span className="inspection-ok">
              ✓ {inspection.metadata?.['general.architecture'] || 'GGUF'} · {inspection.tensorCount} tensors · {inspection.metadata?.[`${inspection.metadata?.['general.architecture']}.context_length`] || '4096'} ctx
              {inspection.warnings?.length > 0 ? ` (${inspection.warnings.join(', ')})` : ''}
            </span>
          )}
        </div>
      )}
      {hint && <small>{hint}</small>}
    </label>
  );
});

const ModelStatusSection = memo(function ModelStatusSection({ modelHealth = {}, busy = false, bridge = null }) {
  const size = (bytes) => !Number.isFinite(Number(bytes)) || Number(bytes) <= 0 ? "Unavailable" : `${(Number(bytes) / 1024 ** 3).toFixed(1)} GB`;
  const failed = modelHealth.loadState === "error" || modelHealth.testState === "failed";
  const checking = modelHealth.loadState === "loading" || modelHealth.testState === "testing";
  const visionChecking = modelHealth.visionTestState === "testing";
  const activeLayers = modelHealth.inferenceMode || "Automatic GPU placement";
  const layerDetail = `${modelHealth.effectiveContext ?? modelHealth.context ?? 2048} token context · up to ${modelHealth.maxOutputTokens ?? 1200} answer tokens`;
  const title = failed ? "Model needs attention" : checking ? "Checking local model…" : modelHealth.loaded ? "Model is loaded and ready" : modelHealth.testState === "passed" ? "Last response test passed" : modelHealth.modelExists && modelHealth.runtimePresent ? "Model files are ready to check" : "Model setup is incomplete";
  const message = modelHealth.lastError || modelHealth.testMessage || (modelHealth.loaded ? `GPT-OSS is ready through ${modelHealth.engineName || "the local engine"}.` : "Run a short local response test to verify model loading and generation.");
  return (
    <section className="settings-section model-health-section">
      <div className="section-kicker"><Activity size={15} /> Local GPT-OSS health</div>
      <div className={`model-health-banner ${failed ? "is-error" : checking ? "is-checking" : modelHealth.loaded || modelHealth.testState === "passed" ? "is-ready" : ""}`}>
        <span className="model-health-indicator" />
        <div><strong>{title}</strong><p>{message}</p></div>
      </div>
      <div className="model-health-grid">
        <div className="model-health-card"><small>GGUF model file</small><strong>{modelHealth.modelExists ? (modelHealth.modelInspection?.architecture ? `${modelHealth.modelInspection.architecture} · Available` : "Available") : "Missing"}</strong><span>{modelHealth.modelInspection?.contextLength ? `${modelHealth.modelInspection.contextLength} ctx · ` : ""}{size(modelHealth.modelSizeBytes)}{modelHealth.modelPath ? ` · ${modelHealth.modelPath.split(/[\\/]/).pop()}` : ""}</span></div>
        <div className="model-health-card"><small>Chat engine</small><strong>{modelHealth.runtimePresent ? (modelHealth.backend === "lmstudio" ? "LM Studio selected" : "Offline GPT engine available") : "Not found"}</strong><span>{modelHealth.backend === "lmstudio" ? (modelHealth.serverRunning ? "External local engine active" : "External local engine starts when needed") : (modelHealth.gpuBackendPresent ? "Bundled CUDA runtime · automatic placement" : "Bundled runtime files are incomplete")}</span></div>
        <div className="model-health-card"><small>Free system RAM</small><strong>{size(modelHealth.freeRamBytes)}</strong><span>of {size(modelHealth.totalRamBytes)} · {size(modelHealth.pagefileAvailableBytes)} paging headroom</span></div>
        <div className="model-health-card"><small>Free GPU memory</small><strong>{size(modelHealth.freeGpuBytes)}</strong><span>GPT-OSS, Qwen3-VL, and FLUX release the GPU before the next model loads.</span></div>
        <div className="model-health-card"><small>Memory lifecycle mode</small><strong>{modelHealth.lifecycleMode === "low_ram" ? "Low RAM" : modelHealth.lifecycleMode === "fast" ? "Fast resident" : "Balanced (Auto)"}</strong><span>{modelHealth.lifecycleMode === "low_ram" ? "Unloads immediately after each turn" : modelHealth.lifecycleMode === "fast" ? "Retained across chats when memory safety allows" : `Idle keep-alive timeout: ${modelHealth.keepaliveSeconds ?? 90}s`}</span></div>
        <div className="model-health-card"><small>Current model state</small><strong>{modelHealth.loaded ? "LOADED" : checking ? "LOADING" : failed ? "ERROR" : "UNLOADED"}</strong><span>{modelHealth.loaded ? "Reused across messages and chats" : (modelHealth.lastUnloadReason || "Idle or waiting for request")}</span></div>
        <div className="model-health-card"><small>Inference settings</small><strong>{activeLayers}</strong><span>{layerDetail}</span></div>
        <div className="model-health-card"><small>Vision + OCR model</small><strong>{modelHealth.visionAvailable ? "Qwen3-VL ready" : modelHealth.visionState === "runtime-unavailable" ? "Local runtime unavailable" : modelHealth.visionState === "missing-projector" ? "Vision projector missing" : modelHealth.visionState === "misconfigured" ? "Vision model not configured" : "Qwen3-VL not installed"}</strong><span>{modelHealth.visionModelKey || modelHealth.visionMessage || "Qwen3-VL-8B · Q4_K_M"}{modelHealth.visionModelSizeBytes ? ` · ${size(modelHealth.visionModelSizeBytes)}` : ""}</span></div>
      </div>
      {modelHealth.testTime && <div className="model-health-last-test"><span>Last response test · {modelHealth.testState}</span><time>{modelHealth.testTime}</time>{modelHealth.testAnswer && <p>{modelHealth.testAnswer}</p>}</div>}
      {modelHealth.visionTestTime && <div className="model-health-last-test"><span>Last vision + OCR test · {modelHealth.visionTestState}</span><time>{modelHealth.visionTestTime}</time><p>{modelHealth.visionTestMessage}</p>{modelHealth.visionTestAnswer && <p>{modelHealth.visionTestAnswer}</p>}</div>}
      <div className="model-health-actions"><button type="button" className="primary-button" disabled={busy || checking || !modelHealth.modelExists || !modelHealth.runtimePresent} onClick={() => bridge?.testModel?.()}>{checking ? <><LoaderCircle size={15} className="spin" /> Testing locally…</> : "Run local response test"}</button><button type="button" className="secondary-button" disabled={busy || visionChecking || !modelHealth.visionAvailable} onClick={() => bridge?.testVisionModel?.()}>{visionChecking ? <><LoaderCircle size={15} className="spin" /> Testing image input + OCR…</> : "Test local vision + OCR"}</button><small>{modelHealth.visionMessage || "Qwen3-VL reads images and scanned pages; no separate OCR model is needed."} GPT-OSS and FLUX are released before another model takes GPU memory.</small></div>
    </section>
  );
});

const SettingsDialog = memo(function SettingsDialog({ bridge, open, setOpen, theme, setTheme, busy, modelHealth, setModelHealth, initialSection = "Chat" }) {
  const [settings, setSettings] = useState({});
  const [voiceDevices, setVoiceDevices] = useState([]);
  const [speech, setSpeech] = useState({});
  const [speechError, setSpeechError] = useState("");
  const [saved, setSaved] = useState(false);
  const [activeSection, setActiveSection] = useState("Chat");
  const [memories, setMemories] = useState([]);
  const [newMemory, setNewMemory] = useState("");
  const [loadingMemories, setLoadingMemories] = useState(false);
  const themeAtOpen = useRef(theme);
  useEffect(() => { if (open) { themeAtOpen.current = theme; setActiveSection(initialSection); call(bridge, "getSettings").then((v) => setSettings(parse(v, {}) || {}));
    call(bridge, "getModelStatus").then((v) => setModelHealth(parse(v, {}) || {}));
    call(bridge, "getSpeechStatus").then(v=>setSpeech(parse(v,{})));
    navigator.mediaDevices?.enumerateDevices?.().then((devices) => setVoiceDevices(devices.filter((d) => d.kind === "audioinput").map((d, index) => ({ id: d.deviceId, name: d.label || `Microphone ${index + 1}` })))).catch(() => setVoiceDevices([]));
  } }, [open, bridge, initialSection]);
  useEffect(()=>{
    if(!open || !bridge?.event)return;
    const receive=raw=>{const e=parse(raw,{});if(e.type==="speechPlayback")setSpeech(e.playback || {});};
    bridge.event.connect(receive);return ()=>bridge.event.disconnect(receive);
  },[open,bridge]);
  const setSpeechPreference=async values=>{
    const result=parse(await call(bridge,"speechSettings",JSON.stringify(values)),{});
    setSpeechError(result.ok ? "" : result.error || "Voice setting could not be saved.");
    if(result.ok)setSettings(old=>({...old,...(values.voice ? {kokoro_voice:values.voice} : {}),...(values.speed ? {speech_speed:values.speed} : {}),...(values.provider ? {speech_engine:values.provider} : {})}));
  };
  const loadMemories = async () => {
    setLoadingMemories(true);
    const raw = await call(bridge, "getMemories");
    setMemories(parse(raw, []) || []);
    setLoadingMemories(false);
  };
  useEffect(() => {
    if (open && activeSection === "Memory") {
      loadMemories();
    }
  }, [open, activeSection]);
  const addMemoryItem = async (e) => {
    e.preventDefault();
    if (!newMemory.trim()) return;
    await call(bridge, "addMemory", newMemory.trim(), "instruction");
    setNewMemory("");
    loadMemories();
  };
  const deleteMemoryItem = async (mid) => {
    await call(bridge, "deleteMemory", mid);
    loadMemories();
  };
  const clearAllMemoryItems = async () => {
    if (window.confirm("Clear all stored memories? Offline GPT will no longer use past instructions across chats.")) {
      await call(bridge, "clearAllMemories");
      loadMemories();
    }
  };
  const update = useCallback((key, value) => {
    setSettings((s) => {
      const next = { ...s, [key]: value };
      if (key === "flux_quality_preset") {
        const presetSteps = { fast: 14, balanced: 20, quality: 28 };
        if (presetSteps[value]) next.flux_steps = presetSteps[value];
      }
      return next;
    });
  }, []);
  const browse = useCallback(async (key, kind = "file") => {
    const raw = await call(bridge, "choosePath", kind);
    const result = typeof raw === "string" ? parse(raw, null) : raw;
    if (result?.path) update(key, result.path);
  }, [bridge, update]);
  const save = () => {
    bridge?.saveSettings?.(JSON.stringify({ ...settings, theme }));
    themeAtOpen.current = theme;
    setSaved(true);
    window.setTimeout(() => { setSaved(false); setOpen(false); }, 500);
  };
  const [inspectedInfo, setInspectedInfo] = useState({});
  const inspectModelFile = useCallback(async (key, path) => {
    if (!path) return;
    try {
      const raw = await call(bridge, "inspectModel", path);
      const res = typeof raw === "string" ? parse(raw, null) : raw;
      if (res?.ok && res.result) {
        setInspectedInfo((prev) => ({ ...prev, [key]: res.result }));
      } else {
        setInspectedInfo((prev) => ({ ...prev, [key]: { error: res?.error || "Inspection failed" } }));
      }
    } catch (err) {
      setInspectedInfo((prev) => ({ ...prev, [key]: { error: String(err) } }));
    }
  }, [bridge]);
  const field = (label, name, extra = {}) => {
    const isGguf = name.endsWith("gguf") || name.includes("model");
    return (
      <SettingField
        key={name}
        label={label}
        name={name}
        value={settings[name]}
        onChange={update}
        onBrowse={browse}
        onInspect={isGguf ? inspectModelFile : null}
        inspection={inspectedInfo[name]}
        {...extra}
      />
    );
  };
  return <Dialog.Root open={open} onOpenChange={(next) => { if (!next) { setTheme(themeAtOpen.current); writeTheme(themeAtOpen.current); } setOpen(next); }}>
    <Dialog.Portal><Dialog.Backdrop className="dialog-overlay" />
      <Dialog.Popup className="settings-dialog">
        <div className="dialog-top"><div><Dialog.Title className="dialog-title">Settings</Dialog.Title><Dialog.Description className="dialog-description">Configure your local AI workspace.</Dialog.Description></div><Dialog.Close className="icon-button" aria-label="Close settings" title="Close settings"><X size={18} /></Dialog.Close></div>
        <div className="settings-layout">
          <nav className="settings-nav" aria-label="Settings sections">
            {["Chat", "Model Status", "Voice Input", "Read Aloud", "Memory", "Local Models", "Compute & RAM", "Image Generation", "Enhancement", "Backup & Diagnostics", "Appearance"].map((section) => <button key={section} className={activeSection === section ? "active" : ""} onClick={() => setActiveSection(section)}>{section}</button>)}
          </nav>
          <div className="settings-content">
            {activeSection === "Chat" && <section className="settings-section"><div className="section-kicker"><Activity size={15} /> Local chat</div>
              {field("Chat engine", "chat_backend", { options: CHAT_BACKEND_OPTIONS, hint: "Offline GPT runs the bundled GPT-OSS model on this PC. LM Studio remains available if you choose it explicitly." })}
              {field("Local GPT-OSS GGUF file", "gpt_oss_gguf", { browseKind: "file", hint: "The built-in engine loads this model directly from the app package." })}
              {settings.chat_backend === "lmstudio" && <>{field("LM Studio model ID", "chat_model")}{field("LM Studio local CLI", "lms_exe", { browseKind: "file", hint: "Only needed when the optional LM Studio engine is selected." })}</>}
              {field("Model memory mode", "model_lifecycle_mode", { options: LIFECYCLE_OPTIONS, hint: "Balanced keeps GPT-OSS resident between turns and unloads when idle or memory is low. Low RAM saves memory for other apps." })}
              {(settings.model_lifecycle_mode === "balanced" || !settings.model_lifecycle_mode) && field("Idle unload timeout (seconds)", "model_keepalive_seconds", { type: "number", hint: "How many seconds to keep the model loaded while waiting for your next prompt (default: 90)." })}
              {field("Reasoning mode", "reasoning_mode", {options:[["auto","Auto · task aware"],["quick","Quick · native low effort"],["standard","Standard · native medium effort"],["deep","Deep · native high effort"]],hint:"Uses the model template effort setting with bounded reasoning tokens. Higher effort can take longer."})}
              {field("Context window", "chat_context", { type: "number", hint: "The built-in engine balances context size with available GPU and system memory." })}
              {field("System prompt file", "chat_system_prompt_path", { browseKind: "file", hint: "A local Markdown file that defines Offline GPT's main assistant behavior." })}
              {field("Validation retries", "chat_validation_retries", { type: "number", hint: "0-3 local correction attempts for exact word counts, JSON, and checked math." })}
              <div className="model-placement-note"><strong>Automatic local placement</strong><small>The built-in engine loads GPT-OSS with a memory-aware GPU profile, streams answers, and releases GPU memory before image or vision work.</small></div>
            </section>}
            {activeSection === "Model Status" && <ModelStatusSection modelHealth={modelHealth} busy={busy} bridge={bridge} />}
            {activeSection === "Voice Input" && <section className="settings-section"><div className="section-kicker"><Mic size={15} /> Local Whisper speech to text</div>
              {field("Whisper model file", "voice_model_path", { browseKind: "file", hint: "Default: multilingual Whisper large-v3-turbo Q5_0. The model stays loaded locally after the first use." })}
              {field("Language", "voice_language", { options: LANGUAGE_OPTIONS, hint: "Choose a language explicitly for multilingual recordings; Automatic uses English." })}
              {field("Microphone", "voice_device", { options: [["", "Default system microphone"], ...voiceDevices.map((device) => [device.id, device.name])], hint: voiceDevices.length ? "Microphones are captured by the local app and never uploaded." : "The default input is used if Windows does not expose named devices yet." })}
              {field("VAD sensitivity", "voice_vad_sensitivity", { options: VAD_OPTIONS, hint: "Optional voice conversation auto-end waits 1.5 seconds of silence. Manual release flushes immediately." })}
              {field("Transcription mode", "voice_transcription_mode", { options: TRANSCRIPTION_MODE_OPTIONS, hint: "Audio is captured as 16 kHz mono PCM and transcribed before any text reaches the chat pipeline." })}
            </section>}
            {activeSection === "Read Aloud" && <section className="settings-section">
              <div className="section-kicker"><Volume2 size={15}/> Offline read aloud</div>
              <p className="voice-description">Natural speech on this PC. No internet or chat-model inference is used for playback.</p>
              <label className="setting-field"><span className="field-label">Voice engine</span><select value={speech.provider || "kokoro"} onChange={e=>setSpeechPreference({provider:e.target.value})}><option value="kokoro" disabled={!speech.kokoroInstalled}>Kokoro · natural local voice{!speech.kokoroInstalled ? " · assets missing" : ""}</option><option value="sapi">Windows · installed system voices</option></select></label>
              <label className="setting-field"><span className="field-label">Reading speed</span><select value={String(speech.speed || 1)} onChange={e=>setSpeechPreference({speed:Number(e.target.value)})}>{[.75,1,1.25,1.5,1.75,2].map(v=><option key={v} value={String(v)}>{v}×</option>)}</select></label>
              <div className="voice-list" role="list" aria-label="Installed voices">{(speech.voiceOptions || []).map(voice=><div className="voice-option" key={voice.id} role="listitem"><label><input type="radio" name="default-speech-voice" checked={(speech.defaultVoice || speech.voice)===voice.id} onChange={()=>setSpeechPreference({voice:voice.id})}/><span>{voice.label}</span></label>{speech.provider==="kokoro" && <button type="button" title={`Preview ${voice.label}`} onClick={async()=>{const result=parse(await call(bridge,"previewVoice",voice.id),{});setSpeechError(result.ok ? "" : result.error);}}><Play size={13}/> Preview</button>}</div>)}</div>
              {["loading","speaking","paused"].includes(speech.state) && <div className="voice-preview-controls"><span>{speech.state==="loading" ? "Preparing voice…" : "Voice preview / playback"}</span><button type="button" title={speech.state==="paused" ? "Resume speech" : "Pause speech"} onClick={()=>bridge?.controlSpeech?.(speech.state==="paused" ? "resume" : "pause")}>{speech.state==="paused" ? <Play size={14}/> : <Pause size={14}/>}</button><button type="button" title="Stop speech" onClick={()=>bridge?.controlSpeech?.("stop")}><Square size={14}/></button></div>}
              {speechError && <p role="alert" className="speech-error">{speechError}</p>}
              <p className="voice-description">The first voice use loads the CPU model. Long answers play in short chunks. Faster speeds trade listening time for clarity; progress follows audio duration.</p>
            </section>}
            {activeSection === "Memory" && <section className="settings-section memory-section"><MemoryControls bridge={bridge}/><div className="section-kicker"><Layers size={15} /> Local memory & personalization</div>
              <div className="model-placement-note"><strong>Personal instructions</strong><small>Offline GPT references saved instructions and personal preferences locally across all conversations on this PC.</small></div>
              <form className="memory-form" onSubmit={addMemoryItem}>
                <input className="memory-input" value={newMemory} onChange={(e) => setNewMemory(e.target.value)} placeholder="E.g., 'Prefer concise responses' or 'Always format code in Python'…" />
                <button type="submit" className="primary-button" disabled={!newMemory.trim()}>Add memory</button>
              </form>
              <div className="memory-list">
                {memories.length > 0 ? memories.map((m) => <div key={m.id} className="memory-item">
                  <div className="memory-text">{m.text}</div>
                  <span className="memory-badge">{m.category || "instruction"}</span>
                  <button type="button" className="history-action-btn delete" onClick={() => deleteMemoryItem(m.id)} title="Delete memory"><Trash2 size={14} /></button>
                </div>) : <div className="memory-empty">{loadingMemories ? "Loading memories…" : "No memories stored yet. Add custom instructions above."}</div>}
              </div>
              {memories.length > 0 && <div className="memory-danger-row"><button type="button" className="clear-memories-btn" onClick={clearAllMemoryItems}>Clear all memories</button></div>}
            </section>}
            {activeSection === "Local Models" && <section className="settings-section"><div className="section-kicker"><Activity size={15} /> Model files</div>
              {field("GPT-OSS model file", "gpt_oss_gguf", { browseKind: "file" })}
              {field("Qwen3-VL model file", "vision_model", { browseKind: "file", hint: "The built-in vision engine reads images and scanned PDF pages from this local GGUF." })}
              {field("Qwen3-VL projector", "vision_projector", { browseKind: "file", hint: "Use the matching mmproj GGUF for this vision model." })}
              {field("Qwen3-VL context window", "vision_context", { type: "number", hint: "4096 is a practical starting point for an 8 GB laptop GPU. The model is unloaded after vision/OCR work." })}
              {field("FLUX.2 Klein model", "flux_model", { browseKind: "file" })}
              {field("Qwen text encoder", "flux_encoder", { browseKind: "file" })}
              {field("FLUX VAE", "flux_vae", { browseKind: "file" })}
              {field("sd-cli.exe", "sd_cli", { browseKind: "file" })}
            </section>}
            {activeSection === "Compute & RAM" && <section className="settings-section"><div className="section-kicker"><Activity size={15} /> Image compute</div>
              {field("Compute backend", "flux_backend", { options: BACKEND_OPTIONS, hint: "Automatic selects an available accelerator. NVIDIA CUDA uses your RTX 4060 when selected." })}
              {field("Image memory profile", "flux_memory", { options: MEMORY_PROFILE_OPTIONS, hint: "Memory safe uses the GPU for computation while keeping model weights on disk; recommended for 8 GB VRAM." })}
              {field("CPU threads (0 = automatic)", "flux_threads", { type: "number", hint: "Leave at 0 to let the image engine choose available CPU cores." })}
            </section>}
            {activeSection === "Image Generation" && <section className="settings-section"><div className="section-kicker"><FileImage size={15} /> Image generation & intelligence</div>
              {field("Creative Director prompt mode", "image_prompt_mode", { options: PROMPT_MODE_OPTIONS, hint: "GPT-OSS acts as a creative director to compose plausible environments and professional lighting without generic buzzwords." })}
              {field("Generation quality preset", "flux_quality_preset", { options: QUALITY_PRESET_OPTIONS, hint: "Controls diffusion sampling steps. Higher steps produce deeper textures." })}
              <div className="field-grid">{field("Image format", "image_output_format", { options: IMAGE_FORMAT_OPTIONS })}{field("JPEG quality", "image_jpeg_quality", { type: "number", hint: "90–100 recommended; 96 default." })}</div>
              <div className="field-grid">{field("Default width", "flux_width", { type: "number" })}{field("Default height", "flux_height", { type: "number" })}</div>
              {field("Image save folder", "image_output_directory", { browseKind: "directory" })}
            </section>}
            {activeSection === "Enhancement" && <section className="settings-section"><div className="section-kicker"><ImagePlus size={15} /> Image enhancement</div>
              {field("ESRGAN / RealESRGAN upscaler", "flux_upscaler", { browseKind: "file", hint: "Optional local model used for requested 4K output and image enhancement." })}
              {field("Enhancement passes", "upscale_repeats", { type: "number", hint: "One pass is recommended for 4K output. Manual enhancement supports 1–4 passes." })}
            </section>}
            {activeSection === "Backup & Diagnostics" && <RecoverySettings bridge={bridge} settings={settings} update={update} busy={busy}/>}
            {activeSection === "Appearance" && <section className="settings-section"><div className="section-kicker"><Sun size={15} /> Appearance</div>
              <div className="appearance-setting"><div><strong>Light appearance</strong><small>Use the brighter surface palette.</small></div><button type="button" role="switch" aria-checked={theme === "light"} onClick={() => { const next = theme === "light" ? "dark" : "light"; setTheme(next); writeTheme(next); }} className="switch-root"><span className="switch-thumb" /></button></div>
            </section>}
          </div>
        </div>
        <div className="dialog-footer"><span className="local-footnote"><ShieldCheck size={14} /> Settings are stored locally on this PC.</span><div className="footer-actions"><Dialog.Close className="secondary-button">Cancel</Dialog.Close><button className="primary-button" onClick={save}>{saved ? <><Check size={16} /> Saved</> : "Save settings"}</button></div></div>
      </Dialog.Popup>
    </Dialog.Portal>
  </Dialog.Root>;
});

function releaseVoiceCapture(captureRef, expectedSession = null) {
  const active = captureRef.current;
  const session = expectedSession && active !== expectedSession ? expectedSession : active;
  if (!expectedSession || active === expectedSession) captureRef.current = null;
  if (!session) return;
  try { if (session.node) { session.node.port.onmessage = null; session.node.disconnect(); } } catch {}
  try { session.source?.disconnect(); } catch {}
  try { session.silent?.disconnect(); } catch {}
  try { session.stream?.getTracks?.().forEach((track) => track.stop()); } catch {}
  try { if (session.context && session.context.state !== "closed") session.context.close(); } catch {}
  try { if (session.workletUrl) URL.revokeObjectURL(session.workletUrl); } catch {}
}

function App() {
  const bridge = useDesktopBridge();
  const [chats, setChats] = useState([]);
  const [chatId, setChatId] = useState("");
  const [editingChatId, setEditingChatId] = useState(null);
  const [editingTitle, setEditingTitle] = useState("");
  const [selectingChats, setSelectingChats] = useState(false);
  const [selectedChatIds, setSelectedChatIds] = useState([]);
  const [trashMode, setTrashMode] = useState(false);
  const [trashedChats, setTrashedChats] = useState([]);
  const [messages, setMessages] = useState([]);
  const [query, setQuery] = useState("");
  const [searchIds,setSearchIds]=useState(null);
  const [savedResponses,setSavedResponses]=useState(null);
  const [notifyDone,setNotifyDone]=useState(()=>{try{return localStorage.getItem('offline-gpt-notify')==='true';}catch{return false;}});
  const notifyRef=useRef(notifyDone);notifyRef.current=notifyDone;
  useEffect(()=>{let active=true;const timer=setTimeout(async()=>{if(!query.trim()){setSearchIds(null);return;}const ids=parse(await call(bridge,'searchConversations',query),[]);if(active)setSearchIds(ids);},180);return()=>{active=false;clearTimeout(timer);};},[bridge,query]);
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState("Local models idle");
  const [modelHealth, setModelHealth] = useState({});
  const [attachment, setAttachment] = useState([]);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [settingsSection, setSettingsSection] = useState("Chat");
  const [theme, setTheme] = useState(readTheme);
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [activeView, setActiveView] = useState("chat");
  const activeViewRef=useRef("chat");activeViewRef.current=activeView;
  const [composerPrefill, setComposerPrefill] = useState("");
  const [libraryImages, setLibraryImages] = useState([]);
  const [libraryLoading, setLibraryLoading] = useState(false);
  const loadLibrary = useCallback(async () => {
    setLibraryLoading(true);
    try {
      const raw = await call(bridge, "getLibraryImages");
      setLibraryImages(parse(raw, []) || []);
    } finally {
      setLibraryLoading(false);
    }
  }, [bridge]);
  const [voiceState, setVoiceState] = useState("idle");
  const [localReminders,setLocalReminders]=useState([]);
  const [requestedRecolor,setRequestedRecolor]=useState(null);
  const [voiceMode,setVoiceMode]=useState(false);
  const [autoEnd,setAutoEnd]=useState(false);
  const voiceOptions=useRef({enabled:false,autoEnd:false,pending:false,spoken:new Set()});
  voiceOptions.current.enabled=voiceMode; voiceOptions.current.autoEnd=autoEnd;
  const [voicePartial, setVoicePartial] = useState(null);
  const [voiceEvent, setVoiceEvent] = useState(null);
  const [voiceError, setVoiceError] = useState("");
  const [dragActive, setDragActive] = useState(false);
  const [elapsedSeconds, setElapsedSeconds] = useState(0);
  const [lightboxData, setLightboxData] = useState(null);
  const handleOpenLightbox = useCallback((path, dimensions) => setLightboxData({ path, dimensions }), []);
  const [showScrollBottom, setShowScrollBottom] = useState(false);
  const scrollViewport = useRef(null);
  const keepAtBottom = useRef(true);
  const scrollToBottom = () => {
    keepAtBottom.current = true;
    setShowScrollBottom(false);
    const viewport = scrollViewport.current;
    if (viewport) {
      viewport.scrollTo({ top: viewport.scrollHeight, behavior: "smooth" });
    }
  };
  const taskStartedAt = useRef(0);
  const completedDuration = useRef(null);
  const renderClock = useRef({ started: 0 });
  const currentChatRef = useRef(chatId);
  useEffect(() => { currentChatRef.current = chatId; }, [chatId]);
  const voiceSequence = useRef(0);
  const voiceResetTimer = useRef(null);
  const voiceCaptureRef = useRef(null);
  const voiceStartToken = useRef(0);

  const stopVoiceCapture = (tellBackend = false) => {
    const session = voiceCaptureRef.current;
    if (session?.starting) voiceStartToken.current += 1;
    if (tellBackend && session?.submitted && !session.starting) {
      session.stopping = true;
      session.releasedEpochMs=Date.now();session.timings.MIC_RELEASED_MS=performance.now()-session.activated;
      bridge?.recordVoiceTiming?.(JSON.stringify({stages:session.timings,releasedEpochMs:session.releasedEpochMs}));
      try { session.source?.disconnect(); } catch {}
      try { session.stream?.getTracks?.().forEach((track) => track.stop()); } catch {}
      // Flush the partial packet explicitly. Acknowledgment is ordered after
      // its PCM message, so release does not discard the final syllable.
      session.finalize=()=>{
        if (voiceCaptureRef.current !== session) return;
        window.clearTimeout(session.flushTimeout);
        session.timings.AUDIO_FINALIZED_MS=performance.now()-session.activated;
        bridge?.recordVoiceTiming?.(JSON.stringify({stages:session.timings,releasedEpochMs:session.releasedEpochMs}));
        bridge?.stopVoiceInput?.();
        releaseVoiceCapture(voiceCaptureRef, session);
      };
      session.node?.port.postMessage({type:"flush"});
      session.flushTimeout=window.setTimeout(session.finalize,250);
      return;
    }
    releaseVoiceCapture(voiceCaptureRef, session || null);
    if (tellBackend && session?.submitted) bridge?.stopVoiceInput?.();
  };

  const activateVoiceCapture = async () => {
    const session = voiceCaptureRef.current;
    if (!session || !session.submitted || session.started) return;
    session.started = true;
    try {
      session.source.connect(session.node); session.node.connect(session.silent); session.silent.connect(session.context.destination);
      await session.context.resume();
      session.timings.MIC_CAPTURE_STARTED_MS=performance.now()-session.activated;
      bridge?.recordVoiceTiming?.(JSON.stringify({stages:session.timings}));
    } catch (error) {
      if (voiceCaptureRef.current !== session) return;
      releaseVoiceCapture(voiceCaptureRef, session);
      setVoiceState("error");
      setVoiceError(error?.message || "Could not start the microphone audio stream.");
      bridge?.stopVoiceInput?.();
    }
  };

  const startVoiceCapture = async () => {
    const token = ++voiceStartToken.current;
    setVoiceError(""); setVoicePartial(""); setVoiceState("loading");
    const session = { token, activated:performance.now(),timings:{MIC_BUTTON_ACTIVATED_MS:0},starting: true, submitted: false, stream: null, context: null, node: null, source: null, silent: null, workletUrl: null };
    voiceCaptureRef.current = session;
    try {
      if (!navigator.mediaDevices?.getUserMedia) throw new Error("This app cannot access browser microphone capture. Check Windows microphone privacy settings and restart Offline GPT.");
      const cfg = parse(await call(bridge, "getSettings"), {}) || {};
      if (token !== voiceStartToken.current) return;
      const audio = { channelCount: { ideal: 1 }, sampleRate: { ideal: 16000 }, echoCancellation: false, noiseSuppression: false, autoGainControl: false };
      if (cfg.voice_device) audio.deviceId = { exact: cfg.voice_device };
      session.stream = await navigator.mediaDevices.getUserMedia({ audio, video: false });
      if (token !== voiceStartToken.current) { releaseVoiceCapture(voiceCaptureRef, session); return; }
      const AudioContextClass = window.AudioContext || window.webkitAudioContext;
      if (!AudioContextClass) throw new Error("This WebView does not support local audio capture.");
      try { session.context = new AudioContextClass({ sampleRate: 16000, latencyHint: "interactive" }); }
      catch { session.context = new AudioContextClass({ latencyHint: "interactive" }); }
      if (!session.context.audioWorklet) throw new Error("The local audio worklet is unavailable in this WebView. Update the bundled WebView2 runtime.");
      const workletSource = `class OfflinePcmCapture extends AudioWorkletProcessor {
        constructor() { super(); this.target = Math.max(800, Math.round(sampleRate * 0.1)); this.samples = new Float32Array(this.target); this.offset = 0; this.port.onmessage=e=>{if(e.data.type==="flush"){this.flush();this.port.postMessage({type:"flushed"})}}; }
        flush(){if(!this.offset)return;const pcm=new Int16Array(this.offset);for(let j=0;j<this.offset;j++){const v=this.samples[j];pcm[j]=v<0?Math.round(v*32768):Math.round(v*32767)}this.offset=0;this.port.postMessage({pcm:pcm.buffer},[pcm.buffer]);}
        process(inputs) {
          const channels = inputs[0]; if (!channels || !channels.length || !channels[0].length) return true;
          const frames = channels[0].length;
          for (let i = 0; i < frames; i++) {
            let sample = 0; for (let c = 0; c < channels.length; c++) sample += channels[c][i] || 0;
            this.samples[this.offset++] = Math.max(-1, Math.min(1, sample / channels.length));
            if (this.offset === this.target) {
              const pcm = new Int16Array(this.target);
              for (let j = 0; j < this.target; j++) { const value = this.samples[j]; pcm[j] = value < 0 ? Math.round(value * 32768) : Math.round(value * 32767); }
              this.offset = 0; this.port.postMessage({ pcm: pcm.buffer }, [pcm.buffer]);
            }
          }
          return true;
        }
      }
      registerProcessor("offline-pcm-capture", OfflinePcmCapture);`;
      session.workletUrl = URL.createObjectURL(new Blob([workletSource], { type: "text/javascript" }));
      await session.context.audioWorklet.addModule(session.workletUrl);
      if (token !== voiceStartToken.current) { releaseVoiceCapture(voiceCaptureRef, session); return; }
      session.source = session.context.createMediaStreamSource(session.stream);
      session.node = new AudioWorkletNode(session.context, "offline-pcm-capture", { numberOfInputs: 1, numberOfOutputs: 1, channelCount: 1, channelCountMode: "max", channelInterpretation: "speakers" });
      session.silent = session.context.createGain(); session.silent.gain.value = 0;
      session.node.port.onmessage = (event) => {
        if (voiceCaptureRef.current !== session || !session.submitted || token !== voiceStartToken.current) return;
        if(event.data.type==="flushed"){session.finalize?.();return;}
        if (!session.stopping) {
          const pcm=new Int16Array(event.data.pcm);let energy=0;
          for (const v of pcm) energy+=(v/32768)**2;
          const now=performance.now();
          if (Math.sqrt(energy/Math.max(1,pcm.length))>.015) {session.voiced=(session.voiced||0)+.1;session.lastVoice=now;session.timings.USER_SPEECH_END_MS=now-session.activated;}
          if (voiceOptions.current.enabled && voiceOptions.current.autoEnd && session.voiced>=.4 && now-(session.lastVoice||now)>1500) {session.timings.END_OF_SPEECH_DETECTED_MS=now-session.activated;stopVoiceCapture(true);setVoiceState("transcribing");return;}
        }
        const bytes = new Uint8Array(event.data.pcm); let binary = "";
        for (let i = 0; i < bytes.length; i += 0x8000) binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
        bridge?.pushVoiceAudio?.(btoa(binary), session.context.sampleRate);
      };
      if (!bridge?.startVoiceInput || !bridge?.pushVoiceAudio) throw new Error("The local microphone bridge is unavailable. Restart Offline GPT and try again.");
      bridge.startVoiceInput();
      session.submitted = true; session.starting = false;
    } catch (error) {
      const messages = {
        NotAllowedError: "Microphone access was denied. Allow Offline GPT to use the microphone in Windows privacy settings, then try again.",
        NotFoundError: "No microphone was found. Connect a microphone or select another input in Voice Input settings.",
        NotReadableError: "The microphone is unavailable or already in use by another application.",
        OverconstrainedError: "The selected microphone is unavailable. Choose Default system microphone in Voice Input settings.",
        SecurityError: "Windows blocked local microphone access. Check Windows microphone privacy settings and restart Offline GPT."
      };
      if (token !== voiceStartToken.current) { releaseVoiceCapture(voiceCaptureRef, session); return; }
      const message = messages[error?.name] || error?.message || "Could not start local microphone capture.";
      releaseVoiceCapture(voiceCaptureRef, session); setVoiceState("error"); setVoiceError(message);
    }
  };

  const toggleVoiceCapture = () => {
    const session = voiceCaptureRef.current;
    if (session?.stopping) return;
    if (session) { stopVoiceCapture(true); if (session.starting) setVoiceState("idle"); else setVoiceState("transcribing"); return; }
    if (["loading", "listening", "transcribing"].includes(voiceState)) return;
    startVoiceCapture();
  };

  useEffect(() => { document.documentElement.dataset.theme = theme; }, [theme]);
  useEffect(() => {
    if (!busy || !taskStartedAt.current) return undefined;
    const updateElapsed = () => setElapsedSeconds(Math.floor((performance.now() - renderClock.current.started) / 1000));
    updateElapsed();
    const timer = window.setInterval(updateElapsed, 1000);
    return () => window.clearInterval(timer);
  }, [busy]);
  useEffect(() => () => { voiceStartToken.current += 1; releaseVoiceCapture(voiceCaptureRef); }, []);
  const syncChats = async () => {
    const raw = await call(bridge, "getChats");
    if (raw) setChats(parse(raw, []));
  };
  const refreshModelHealth = async () => {
    const raw = await call(bridge, "getModelStatus");
    const result = typeof raw === "string" ? parse(raw, {}) : raw;
    if (result && typeof result === "object") setModelHealth(result);
    return result || {};
  };
  useEffect(()=>{const timer=setInterval(()=>{if(document.visibilityState!=="hidden")refreshModelHealth();},15000);return()=>clearInterval(timer);},[bridge]);
  const loadChat = async (id) => {
    voiceStartToken.current++; releaseVoiceCapture(voiceCaptureRef); bridge?.cancelVoiceInput?.(); voiceOptions.current.pending=false; setVoiceEvent(null); setVoiceState("idle");
    if (busy) return;
    completedDuration.current = null;
    keepAtBottom.current = true;
    setAttachment([]);
    currentChatRef.current=id;
    setChatId(id);
    const raw = await call(bridge, "loadChat", id);
    setMessages(parse(raw, []));
    setActiveView("chat");
  };
  useEffect(() => {
    if (!bridge) return;
    syncChats();
    refreshModelHealth();
    loadLibrary();
    call(bridge, "getSettings").then((raw) => { const cfg = parse(raw, {}) || {}; if (cfg.theme === "dark" || cfg.theme === "light") { setTheme(cfg.theme); writeTheme(cfg.theme); } });
    call(bridge, "currentChat").then((id) => { if (id) loadChat(id); });
    const onHistory = () => syncChats();
    const onStatus = (value) => setStatus(value);
    let pendingEvents = [];
    let streamTimer = 0;
    const flushStream = () => {
      window.clearTimeout(streamTimer); streamTimer = 0;
      const events = pendingEvents; pendingEvents = [];
      if (!events.length) return;
      if (!renderClock.current.firstContentReceived && events.some(e=>e.type==="append" && e.text?.trim() || e.type==="messageUpdate" && e.message?.content?.trim())) renderClock.current.firstContentReceived=performance.now();
      setMessages((items) => {
        const next = [...items];
        for (const event of events) {
          const index = next.findIndex((m) => m.id === event.id);
          if (index < 0) { if (event.type === "chat") next.push({...event.message, enterAnimate: true}); continue; }
          const old = next[index];
          if (event.type === "append") next[index] = {...old, streaming: true, content: (old.content || "") + event.text};
          else if (event.type === "status") next[index] = {...old, status: event.status, activeKind: event.activeKind};
          else if (event.type === "activity") next[index] = {...old, activity: event.activity};
          else next[index] = {...old, ...event.message};
        }
        return next;
      });
    };
    const onEvent = (raw) => {
      const event = parse(raw, {});
      if (event.type?.startsWith("voice") && event.chatId && currentChatRef.current && event.chatId!==currentChatRef.current) return;
      if (["chat", "append", "status", "messageUpdate", "activity"].includes(event.type)) {
        if (event.chatId && currentChatRef.current && event.chatId !== currentChatRef.current) return;
        if (event.type === "activity") setStatus(event.activity?.phase || "Processing");
        if(!renderClock.current.firstContentReceived && (event.type==="append" && event.text?.trim() || event.type==="messageUpdate" && event.message?.content?.trim()))renderClock.current.firstContentReceived=performance.now();
        pendingEvents.push(event);
        if (!streamTimer) streamTimer = window.setTimeout(flushStream, 32);
        return;
      }
      flushStream();
      if(event.type==='navigateChat'){loadChat(event.targetChatId).then(()=>setTimeout(()=>document.querySelector(`[data-source-message="${Number(event.messageId)}"]`)?.scrollIntoView({block:'center',behavior:'smooth'}),150));return;}
      if(event.type==='localReminder'){setLocalReminders(v=>v.some(r=>r.id===event.reminder.id)?v:[...v,event.reminder]);return;}
      if(event.type==='reminderDismissed'){setLocalReminders(v=>v.filter(r=>r.id!==event.id));return;}
      if (event.type === "modelHealth") setModelHealth(event.status || {});
      if (event.type === "busy") {
        if (event.chatId && !currentChatRef.current) { currentChatRef.current=event.chatId; setChatId(event.chatId); }
        const isBusy = !!event.value;
        if (isBusy && !taskStartedAt.current) { taskStartedAt.current = Date.now(); renderClock.current = {started: performance.now()}; completedDuration.current = null; setElapsedSeconds(0); }
        if (!isBusy && taskStartedAt.current) {
          const duration = Math.max(0, Math.round((performance.now() - renderClock.current.started) / 1000));
          taskStartedAt.current = 0;
          completedDuration.current = duration;
          if(notifyRef.current&&duration>=30)bridge?.notifyTaskComplete?.(`Local task finished after ${formatDuration(duration)}. Open OfflineGPT to review its result.`);
          setElapsedSeconds(duration);
          setMessages((items) => applyCompletedDuration(items, duration).map((m) => m.status || m.streaming ? { ...m, status: "", streaming: false } : m));
        } else if (!isBusy) setMessages((items) => items.map((m) => m.status || m.streaming ? { ...m, status: "", streaming: false } : m));
        setBusy(isBusy);
        setStatus(event.status || (isBusy ? "Working…" : "Ready"));
      }
      if (event.type === "messages") {
        const duration = completedDuration.current;
        setMessages(applyCompletedDuration(event.messages || [], duration));
        const last=(event.messages||[]).at(-1), opts=voiceOptions.current;
        if (opts.enabled && opts.pending && last?.role==="assistant" && last.content && !last.error && !/^(?:Error|Image engine error|Document error|Vision error):/i.test(last.content) && !opts.spoken.has(last.id)) {
          opts.pending=false;opts.spoken.add(last.id);call(bridge,"speakResponse",String(last.id),stripUiControlArtifacts(last.content));
        }
        if (duration != null) completedDuration.current = null;
      }
      if (event.type === "chatChanged") { bridge?.controlSpeech?.("stop"); currentChatRef.current=event.id; completedDuration.current = null; keepAtBottom.current = true; setChatId(event.id); setMessages(event.messages || []); setAttachment([]); }
      if (event.type === "image") {
        const duration = completedDuration.current;
        setMessages((items) => applyCompletedDuration([...items, { id: event.id, role: "assistant", type: "image", attachment: event.path, images: [event.path], dimensions: event.dimensions, content: event.content }], duration));
        if (duration != null) completedDuration.current = null;
        loadLibrary();
      }
      if (event.type === "libraryUpdated") {
        loadLibrary();
      }
      if (event.type === "error") {
        const duration = completedDuration.current;
        setMessages((items) => applyCompletedDuration([...items, { id: event.id, role: "assistant", content: event.message, error: true }], duration));
        if (duration != null) completedDuration.current = null;
      }
      if (event.type === "voiceState") {
        const next = typeof event.value === "string" ? event.value : event.value ? "listening" : "idle";
        setVoiceState(next);
        if (next === "listening") activateVoiceCapture();
        if (["transcribing", "completed", "error", "idle"].includes(next)) releaseVoiceCapture(voiceCaptureRef);
        if (["loading", "listening", "transcribing"].includes(next)) {
          window.clearTimeout(voiceResetTimer.current);
          setVoiceError("");
        }
        if (next === "completed") {
          window.clearTimeout(voiceResetTimer.current);
          voiceResetTimer.current = window.setTimeout(() => setVoiceState((current) => current === "completed" ? "idle" : current), 2800);
        }
      }
      if (event.type === "voicePartial") setVoicePartial(event.text || "");
      if(event.type === "recolorRequested"){setRequestedRecolor(event.image);setActiveView("library");loadLibrary();}
      if (event.type === "voiceText") { setVoiceEvent({ ...event,id: ++voiceSequence.current,receivedEpochMs:Date.now(),text: event.text || "", alternatives: event.alternatives || [] }); setVoiceError(""); }
      if (event.type === "voiceError") { window.clearTimeout(voiceResetTimer.current); setVoiceState("error"); setVoiceError(event.message || "Offline Whisper input failed."); }
    };
    const onDropped = (raw) => {
      if(activeViewRef.current==="documents")return;
      const files = parse(raw, []);
      if (!Array.isArray(files) || !files.length) return;
      setAttachment((current) => [...current, ...files.filter((file) => file?.path && !current.some((old) => old.path === file.path))]);
      setActiveView("chat");
    };
    const onDragState = (active) => setDragActive(!!active);
    bridge.historyChanged?.connect(onHistory);
    bridge.filesDropped?.connect(onDropped);
    bridge.fileDragState?.connect(onDragState);
    bridge.event?.connect(onEvent);
    bridge.statusChanged?.connect(onStatus);
    return () => {
      window.clearTimeout(streamTimer);
      bridge.historyChanged?.disconnect(onHistory);
      bridge.filesDropped?.disconnect(onDropped);
      bridge.fileDragState?.disconnect(onDragState);
      bridge.event?.disconnect(onEvent);
      bridge.statusChanged?.disconnect(onStatus);
    };
  }, [bridge, loadLibrary]);
  useEffect(() => {
    const viewport = scrollViewport.current;
    const content = viewport?.querySelector(".conversation-inner");
    if (!viewport || !content) return;
    let frame = 0;
    const follow = () => {
      if (!keepAtBottom.current) return;
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => { viewport.scrollTop = viewport.scrollHeight; });
    };
    // Lazy images, expanded details and fonts can change height after commit.
    // Follow those changes only while the user is already following the end.
    const observer = new ResizeObserver(follow);
    observer.observe(content);
    follow();
    return () => { observer.disconnect(); cancelAnimationFrame(frame); };
  }, [activeView, messages.length > 0]);
  useEffect(() => {
    const viewport = scrollViewport.current;
    if (viewport && keepAtBottom.current) requestAnimationFrame(() => { viewport.scrollTop = viewport.scrollHeight; });
  }, [messages]);

  useEffect(() => {
    if (!bridge?.getModelResidency) return;
    let alive = true;
    const poll = () =>
      call(bridge, "getModelResidency").then((raw) => {
        const state = parse(raw, {});
        if (!alive || !state || typeof state !== "object") return;
        setModelHealth((old) => {
          let changed = false;
          for (const [k, v] of Object.entries(state)) {
            if (old[k] !== v) {
              changed = true;
              break;
            }
          }
          return changed ? { ...old, ...state } : old;
        });
      });
    poll();
    const timer = window.setInterval(poll, 2000);
    return () => {
      alive = false;
      window.clearInterval(timer);
    };
  }, [bridge]);

  const visibleChats = useMemo(() => (trashMode ? trashedChats : chats).filter((c) => !query.trim() || (c.title || "New chat").toLowerCase().includes(query.toLowerCase()) || searchIds?.includes(c.id)), [chats, query, trashMode, trashedChats,searchIds]);
  const groupedChats = useMemo(() => groupChatsByDate(visibleChats), [visibleChats]);

  const renderChatRow = (chat) => (
    <div key={chat.id} className={"history-row " + (chat.id === chatId ? "active" : "")}>
      {selectingChats ? <label className="chat-select-row"><input type="checkbox" checked={selectedChatIds.includes(chat.id)} onChange={() => setSelectedChatIds(ids => ids.includes(chat.id) ? ids.filter(id => id !== chat.id) : [...ids, chat.id])}/><span>{chat.title || "New chat"}</span></label> : editingChatId === chat.id ? <div className="history-rename-wrap">
        <input
          autoFocus
          className="history-rename-input"
          value={editingTitle}
          onChange={(e) => setEditingTitle(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") submitRename(chat.id);
            if (e.key === "Escape") cancelRename();
          }}
          onBlur={() => submitRename(chat.id)}
        />
        <button type="button" className="history-rename-btn" title="Save" onMouseDown={(e) => { e.preventDefault(); submitRename(chat.id); }}>
          <Check size={13} />
        </button>
        <button type="button" className="history-rename-btn" title="Cancel" onMouseDown={(e) => { e.preventDefault(); cancelRename(); }}>
          <X size={13} />
        </button>
      </div> : <>
        <button type="button" disabled={busy} className="history-item" onClick={() => loadChat(chat.id)} onDoubleClick={(e) => startRename(e, chat)}>
          <MessageSquare size={14} />
          <span>{chat.title || "New chat"}</span>
        </button>
        <button type="button" className="history-action-btn" title={chat.pinned?"Unpin chat":"Pin chat"} aria-label={chat.pinned?"Unpin chat":"Pin chat"} disabled={busy} onClick={async()=>{await call(bridge,"pinChat",chat.id,!chat.pinned);await syncChats()}}><Pin size={13}/></button>
        <button type="button" className="history-action-btn" title="Branch chat" aria-label="Branch chat" disabled={busy} onClick={async()=>{const r=parse(await call(bridge,"branchChat",chat.id,""),{});if(r.ok){await syncChats();await loadChat(r.chatId)}else setStatus(r.error||"Could not branch this conversation.")}}><Layers size={13}/></button>
        <button type="button" className="history-action-btn" title="Rename chat" aria-label="Rename chat" disabled={busy} onClick={(event) => startRename(event, chat)}>
          <Pencil size={13} />
        </button>
        <button type="button" className="history-action-btn delete" title={`Delete ${chat.title || "chat"}`} aria-label={`Delete ${chat.title || "chat"}`} disabled={busy} onClick={(event) => deleteChat(event, chat)}>
          <Trash2 size={13} />
        </button>
      </>}
    </div>
  );
  const deleteChat = async (event, chat) => {
    event.stopPropagation();
    if (busy || !window.confirm(`Delete “${chat.title || "New chat"}”? It will move to Recently deleted; attachments are preserved.`)) return;
    const raw = await call(bridge, "deleteChat", chat.id);
    const result = typeof raw === "string" ? parse(raw, {}) : raw;
    if (!result?.ok) { setStatus(result?.error || "Could not delete this chat."); return; }
    await syncChats();
  };
  const cancelSelection = () => {setSelectingChats(false);setSelectedChatIds([]);setTrashMode(false);};
  const openTrash = async () => {
    setTrashedChats(parse(await call(bridge, "getTrashedChats"), []));
    setTrashMode(true);setSelectingChats(true);setSelectedChatIds([]);setQuery("");
  };
  const manageSelected = async () => {
    const ids=[...selectedChatIds];
    if (!ids.length || busy) return;
    if (!trashMode && !window.confirm(`Move exactly ${ids.length} selected conversation${ids.length === 1 ? "" : "s"} to Recently deleted? Attachment files will be preserved.`)) return;
    const result=parse(await call(bridge,"manageChats",JSON.stringify(ids),trashMode),{});
    if (!result?.ok) {setStatus(result?.error || "Conversation operation failed.");return;}
    setStatus(`${result.count} conversations ${trashMode ? "restored" : "moved to Recently deleted"}.`);
    setSelectedChatIds([]);await syncChats();
    if (trashMode) setTrashedChats(parse(await call(bridge,"getTrashedChats"),[]));
  };
  const exportSelected = async () => {
    const result=parse(await call(bridge,"exportChats",JSON.stringify(selectedChatIds)),{});
    if (!result.cancelled) setStatus(result.ok ? `${result.count} conversations exported locally.` : result.error || "Export failed.");
  };
  const startRename = (event, chat) => {
    event.stopPropagation();
    setEditingChatId(chat.id);
    setEditingTitle(chat.title || "");
  };
  const submitRename = async (cid) => {
    if (editingChatId !== cid) return;
    const clean = editingTitle.trim();
    if (clean) {
      await call(bridge, "renameChat", cid, clean);
      await syncChats();
    }
    setEditingChatId(null);
  };
  const cancelRename = () => {
    setEditingChatId(null);
    setEditingTitle("");
  };
  const exportConversation = () => {
    if (!chatId || messages.length === 0) return;
    bridge?.exportChat?.(chatId);
  };
  const newChat = async () => {
    voiceStartToken.current++; releaseVoiceCapture(voiceCaptureRef); bridge?.cancelVoiceInput?.(); voiceOptions.current.pending=false; setVoiceEvent(null); setVoiceState("idle");
    if (busy) return;
    completedDuration.current = null;
    keepAtBottom.current = true;
    setAttachment([]);
    setMessages([]);
    setActiveView("chat");
    const raw = await call(bridge, "newChat");
    const id = typeof raw === "string" ? parse(raw, raw) : raw;
    if (id) { currentChatRef.current=id; setChatId(id); }
    await syncChats();
  };
  const openImagePrompt = (prompt) => { setComposerPrefill(prompt); setActiveView("chat"); };
  const send = (text, file) => {
    voiceOptions.current.pending=voiceOptions.current.enabled;
    keepAtBottom.current = true;
    taskStartedAt.current = Date.now();
    renderClock.current = {started: performance.now()};
    completedDuration.current = null;
    setElapsedSeconds(0);
    const id = "local-" + Date.now();
    setMessages((items) => [...items, { id, role: "user", content: text, enterAnimate: true, attachments: file.map((item) => item.path), attachment: file[0]?.path || "", fileKind: file[0]?.kind, time: new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) }]);
    setBusy(true);
    setStatus("Routing to a local model…");
    bridge?.sendMessage?.(text, JSON.stringify(file.map((item) => item.path)), taskStartedAt.current);
  };
  const handleTheme = () => { const next = theme === "dark" ? "light" : "dark"; setTheme(next); writeTheme(next); bridge?.saveSettings?.(JSON.stringify({ theme: next })); };
  const openSettings = (section = "Chat") => { setSettingsSection(section); setSettingsOpen(true); };
  useEffect(() => {
    const onShortcut = (event) => {
      if (!(event.ctrlKey || event.metaKey) || event.altKey) return;
      const key = event.key.toLowerCase();
      if (key === "n") { event.preventDefault(); newChat(); }
      else if (key === "k") {event.preventDefault();document.querySelector(".history-search")?.focus();}
      else if (key === "m" && event.shiftKey && !busy) {event.preventDefault();toggleVoiceCapture();}
      else if (key === "b") { event.preventDefault(); setSidebarOpen((open) => !open); }
      else if (key === ",") { event.preventDefault(); setSettingsOpen(true); }
      else if (key === "e") { if (chatId && messages.length > 0) { event.preventDefault(); exportConversation(); } }
    };
    window.addEventListener("keydown", onShortcut);
    return () => window.removeEventListener("keydown", onShortcut);
  }, [busy, bridge, chatId, messages.length]);

  const composerProps = { bridge, chatId, busy, onSend: send, attachment, setAttachment, voiceState, voicePartial, voiceEvent, voiceError, clearVoiceError: () => setVoiceError(""), onVoiceInserted: () => setVoiceEvent(null), onVoiceToggle: toggleVoiceCapture, dragActive, initialText: composerPrefill, onPrefillConsumed: () => setComposerPrefill("") };
  return <div className="app-shell">
      <aside className={"sidebar " + (!sidebarOpen ? "sidebar-collapsed" : "")}>
        <div className="brand-row"><div className="brand-copy"><span className="brand-title-clean">OfflineGPT</span><small>Private AI workspace</small></div><IconButton className="sidebar-close" title={sidebarOpen ? "Collapse sidebar" : "Expand sidebar"} onClick={() => setSidebarOpen((open) => !open)}><PanelLeftClose size={17} /></IconButton></div>
        <button className="new-chat-button" onClick={newChat} title="New chat" aria-keyshortcuts="Control+N Meta+N"><Plus size={16} /><span>New chat</span><span className="shortcut">Ctrl N</span></button>
        <div className="side-primary">
          <button className={"nav-item " + (activeView === "chat" ? "selected" : "")} onClick={() => setActiveView("chat")} title="Chats"><MessageSquare size={16} /><span>Chats</span></button>
          <button className={"nav-item " + (activeView === "library" ? "selected" : "")} onClick={() => { setActiveView("library"); loadLibrary(); }} title="Image Library"><Images size={16} /><span>Library</span></button>
          <button className={"nav-item " + (activeView === "projects" ? "selected" : "")} onClick={()=>setActiveView("projects")} title="Projects"><FolderOpen size={16}/><span>Projects</span></button>
          <button className={"nav-item " + (activeView === "documents" ? "selected" : "")} onClick={() => setActiveView("documents")} title="Documents"><FileText size={16} /><span>Documents</span></button>
          <button className={"nav-item " + (activeView === "studio" ? "selected" : "")} onClick={() => setActiveView("studio")} title="Image studio"><WandSparkles size={16} /><span>Image studio</span></button>
        </div>
        <div className="history-head"><span>{trashMode ? "Recently deleted" : "Recent"}</span><div className="history-controls"><IconButton title="Select chats" disabled={busy} onClick={() => {setSelectingChats(true);setSelectedChatIds([]);}}><Check size={15}/></IconButton><IconButton title="Recently deleted" disabled={busy} onClick={openTrash}><Trash2 size={14}/></IconButton><IconButton title="Search conversations" onClick={() => document.querySelector(".history-search")?.focus()}><Search size={15} /></IconButton></div></div>
        <div className="history-search-wrap"><Search size={14} /><input className="history-search" value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search chats" /></div>
        {selectingChats && <section className="chat-selection" aria-label="Chat selection">
          <div><strong>{selectedChatIds.length} selected</strong><button onClick={cancelSelection}>Cancel</button></div>
          <div><button disabled={busy} onClick={() => setSelectedChatIds(visibleChats.map(c => c.id))}>Select all shown</button><button onClick={() => setSelectedChatIds([])}>Deselect all</button></div>
          <div><button disabled={busy || !selectedChatIds.length} onClick={manageSelected}>{trashMode ? "Restore" : "Delete"}</button><button disabled={!selectedChatIds.length} onClick={exportSelected}>Export</button></div>
        </section>}
        <div className="history-scroll"><div className="history-viewport">
          {query.trim() ? (
            visibleChats.map(renderChatRow)
          ) : (
            groupedChats.map((group) => (
              <div key={group.title} className="history-group">
                <div className="history-category-title">{group.title}</div>
                {group.items.map(renderChatRow)}
              </div>
            ))
          )}
          {visibleChats.length === 0 && <div className="empty-history">{trashMode ? "No deleted conversations." : "Your conversations will appear here."}</div>}
        </div></div>
        <div className="sidebar-bottom">
          <LocalStatus modelHealth={modelHealth} onRefresh={refreshModelHealth} onOpenSettings={() => openSettings("Model Status")} />
          <button className="nav-item" onClick={() => openSettings("Chat")} title="Settings"><Settings2 size={16} /><span>Settings</span><ChevronRight size={15} className="nav-chevron" /></button>
          <button className="profile-row" onClick={() => openSettings("Chat")}><div className="profile-info"><strong className="profile-name-clean">OfflineGPT</strong><small>On this device</small></div><MoreHorizontal size={17} /></button>
        </div>
      </aside>
    <main className="main-panel">
      <header className="topbar">
        <div className="topbar-left">{!sidebarOpen && <IconButton title="Expand sidebar" onClick={() => setSidebarOpen(true)}><Menu size={18} /></IconButton>}<div className="context-title"><span>{activeView === "projects" ? "Projects" : activeView === "library" ? "Image Library" : activeView === "documents" ? "Documents" : activeView === "studio" ? "Image studio" : (chats.find((chat) => chat.id === chatId)?.title || "New chat")}</span></div><ModelSelector modelHealth={modelHealth} onRefresh={refreshModelHealth} onOpenSettings={() => openSettings("Chat")} /></div>
        <div className="mode-switch"><button className={activeView === "chat" ? "active" : ""} aria-pressed={activeView === "chat"} onClick={() => setActiveView("chat")}>Chat</button><button className={activeView === "library" ? "active" : ""} aria-pressed={activeView === "library"} onClick={() => { setActiveView("library"); loadLibrary(); }}>Library</button><button className={activeView === "documents" || activeView === "studio" ? "active" : ""} aria-pressed={activeView === "documents" || activeView === "studio"} onClick={() => setActiveView(activeView === "studio" ? "studio" : "documents")}>Work</button></div>
        <div className="topbar-right"><span className="resource-summary" title="Available system RAM and GPU memory; sampled every 15 seconds. GPU utilization is not measured here.">{modelHealth.freeRamBytes!=null?`${(modelHealth.freeRamBytes/1024**3).toFixed(1)} GB RAM free`:""}{modelHealth.freeGpuBytes!=null?` · ${(modelHealth.freeGpuBytes/1024**3).toFixed(1)} GB VRAM free`:""}</span>
          <IconButton title="Bookmarked responses" onClick={async()=>setSavedResponses(parse(await call(bridge,'getBookmarks'),[]))}><Bookmark size={17}/></IconButton>
          <IconButton title={notifyDone?'Disable long-task notifications':'Enable long-task notifications'} onClick={()=>{setNotifyDone(v=>{try{localStorage.setItem('offline-gpt-notify',String(!v));}catch{}return !v;});}}><Bell size={17}/></IconButton>
          <IconButton title={voiceMode ? "Leave voice conversation" : "Voice conversation · microphone starts only when activated"} onClick={()=>{const next=!voiceMode;setVoiceMode(next);if(!next){voiceStartToken.current++;releaseVoiceCapture(voiceCaptureRef);bridge?.cancelVoiceInput?.();bridge?.controlSpeech?.("stop");voiceOptions.current.pending=false;}}}><Volume2 size={17}/></IconButton>
          
          <IconButton title={chatId && messages.length > 0 ? "Export chat to Markdown (Ctrl+E)" : "Select a conversation to export"} disabled={!chatId || messages.length === 0} onClick={exportConversation}>
            <Download size={17} />
          </IconButton>
          <IconButton title={theme === "dark" ? "Switch to light theme" : "Switch to dark theme"} onClick={handleTheme}>{theme === "dark" ? <Sun size={17} /> : <Moon size={17} />}</IconButton>
          <IconButton title="Settings" onClick={() => openSettings("Chat")}><Settings2 size={17} /></IconButton>
        </div>
      </header>
      {savedResponses&&<Dialog.Root open onOpenChange={open=>{if(!open)setSavedResponses(null)}}><Dialog.Portal><Dialog.Backdrop className="library-compare-backdrop"/><Dialog.Popup className="library-compare-dialog"><Dialog.Title>Bookmarked responses</Dialog.Title><Dialog.Description>Saved responses remain attached to their original conversations.</Dialog.Description>{!savedResponses.length&&<p>No bookmarked responses.</p>}{savedResponses.map(item=><button key={item.messageId} className="bookmark-result" onClick={async()=>{setSavedResponses(null);await loadChat(item.chatId);setTimeout(()=>document.querySelector(`[data-source-message="${item.messageId}"]`)?.scrollIntoView({block:'center',behavior:'smooth'}),150);}}><strong>{item.title}</strong><span>{item.content}</span></button>)}<Dialog.Close className="details-btn">Close bookmarks</Dialog.Close></Dialog.Popup></Dialog.Portal></Dialog.Root>}
      {Number(modelHealth.freeRamBytes)>0&&Number(modelHealth.freeRamBytes)<1024**3&&<aside className="local-reminder" role="status">Low available RAM ({(modelHealth.freeRamBytes/1024**3).toFixed(1)} GB). Save your work and close unused apps before loading another model.</aside>}
      {localReminders.length>0 && <aside className="local-reminder" role="status">{localReminders.map(r=><span key={r.id}>{r.title} <button onClick={async()=>{await workspaceCall(bridge,'reminderDismiss',{id:r.id});setLocalReminders(v=>v.filter(x=>x.id!==r.id))}}>Dismiss</button></span>)}</aside>}
      {voiceMode && <section className="voice-conversation" aria-label="Local voice conversation"><span><Volume2 size={15}/> Voice conversation · {busy ? "Thinking" : voiceState==="listening" ? "Microphone on" : ["loading","transcribing"].includes(voiceState) ? "Transcribing locally" : "Microphone off"}</span><button disabled={busy} onClick={toggleVoiceCapture}>{voiceState==="listening" ? "Stop recording" : "Push to talk"}</button><button onClick={()=>bridge?.controlSpeech?.("stop")}>Stop speech</button><label><input type="checkbox" checked={autoEnd} onChange={e=>setAutoEnd(e.target.checked)}/> Stop after silence</label><button onClick={()=>{setVoiceMode(false);voiceStartToken.current++;releaseVoiceCapture(voiceCaptureRef);bridge?.cancelVoiceInput?.();bridge?.controlSpeech?.("stop");}}>Return to text</button><small>Review and edit your transcript, then send. Replies are read aloud locally.</small></section>}
      {activeView === "library" ? (
        <ImageLibrary
          requestedImage={requestedRecolor} onRequestedConsumed={()=>setRequestedRecolor(null)}
          images={libraryImages}
          onVariationStarted={()=>{keepAtBottom.current=true;setActiveView("chat");}}
          loading={libraryLoading}
          onRefresh={async () => {
            await call(bridge, "refreshLibrary", true);
            await loadLibrary();
          }}
          bridge={bridge}
          onSelectChat={(cid) => { if (cid) loadChat(cid); }}
          onPrefillPrompt={openImagePrompt}
          onOpenLightbox={handleOpenLightbox}
        />
      ) : activeView === "projects" ? <ProjectWorkspace bridge={bridge} chatId={chatId} onSelectChat={loadChat}/> : activeView === "documents" ? <DocumentWorkspace bridge={bridge} onPrompt={openImagePrompt} onAttach={async()=>{const files=parse(await call(bridge,"pickAttachment","document"),[]);setAttachment(Array.isArray(files)?files:[]);setActiveView("chat")}}/> : activeView === "studio" ? <section className="workspace-view">
        <div className="workspace-heading"><div className="eyebrow">FLUX.2 KLEIN</div><h1>Image studio</h1><p>Describe the result you want. Size requests like “4K” use the configured local upscaler to reach the requested output dimensions.</p></div>
        <div className="studio-card"><div className="studio-badge"><FileImage size={16} /> Ready for your prompt</div><div className="studio-presets"><button onClick={() => openImagePrompt("Create a 1024x576 image of ")}>Wide · 16:9 <small>1024 × 576</small></button><button onClick={() => openImagePrompt("Create a 768x768 image of ")}>Square · 1:1 <small>768 × 768</small></button><button onClick={() => openImagePrompt("Create a 3840x2160 image of ")}>4K UHD <small>upscaled locally</small></button></div><div className="studio-tip"><Info size={15} /> Larger outputs are rendered at a GPU-friendly size and enlarged with the local upscaler.</div><button className="primary-button" onClick={() => openImagePrompt("Create an image of ")}><WandSparkles size={16} /> Start a prompt</button></div>
      </section> : messages.length === 0 ? <section className="welcome-view">
        <div className="welcome-center">
          <div className="welcome-badge"><ShieldCheck size={13} /><span>Offline & Private AI Workspace</span></div>
          <h1>What would you like to create today?</h1>
          <Composer key={chatId||"new"} {...composerProps} className="welcome-composer" />
          <WelcomeSuggestions onSelectPrompt={(p) => openImagePrompt(p)} />
        </div>
      </section> : <><section className="conversation">
        <div className="conversation-scroll"><div ref={scrollViewport} onScroll={(event) => { const el = event.currentTarget; const dist = el.scrollHeight - el.scrollTop - el.clientHeight; keepAtBottom.current = dist < 120; setShowScrollBottom(dist > 200); }} className="conversation-viewport"><div className="conversation-inner">
          {messages.map((message) => <Message key={message.id} message={message} bridge={bridge} onOpenLightbox={handleOpenLightbox} canRegenerate={!busy && message === messages[messages.length-1]} renderClock={message === messages[messages.length-1] ? renderClock.current : null} elapsedSeconds={message.status ? elapsedSeconds : 0} onPrefillPrompt={openImagePrompt} />)}
          {busy && !messages.some((m) => m.status) && <div className="thinking"><span /><span /><span /><small>{status} · Worked for {formatDuration(elapsedSeconds)}</small></div>}
        </div></div></div>
        {showScrollBottom && <button type="button" className="scroll-to-bottom-btn" onClick={scrollToBottom} aria-label="Scroll to bottom" title="Scroll to bottom"><ArrowDown size={16} /></button>}
      </section><div className="bottom-composer"><Composer key={chatId||"new"} {...composerProps} /></div></>}
    </main>
    <SettingsDialog bridge={bridge} open={settingsOpen} setOpen={setSettingsOpen} theme={theme} setTheme={setTheme} busy={busy} modelHealth={modelHealth} setModelHealth={setModelHealth} initialSection={settingsSection} />
    {lightboxData && (
      <ImageLightbox
        imagePath={lightboxData.path}
        dimensions={lightboxData.dimensions}
        onClose={() => setLightboxData(null)}
        onSave={(p) => bridge?.saveImage?.(p)}
        onCopy={(p) => call(bridge, "copyImage", p)}
        onReveal={(p) => bridge?.revealFile?.(p)}
      />
    )}
  </div>;
}

createRoot(document.getElementById("root")).render(<App />);
