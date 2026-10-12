// The page's one connection to the core: every terminal on the page goes
// through it, each on a channel of its own. See tterm/api/ws.py for the
// other end and the message format.
//
// The connection may drop: the laptop sleeps, the core restarts, the network
// blinks. The terminals do not care, they live in the core. The socket comes
// back by itself, and each channel asks for what it missed after the last
// number it has, so the screen goes on where it was.

import type { TerminalInfo } from "./api";

const OUTPUT = 1;
const INPUT = 2;
const RETRY_MS = [300, 1000, 2000, 4000, 8000];

export type MarkerEvent =
  | { t: "P"; code: number | null; cwd: string; home: string; branch: string;
      venv: string }
  | { t: "C"; command: string };

export interface ChannelHandlers {
  /** The core knows this terminal; `resumed` says whether it went on from
   * the number we had or starts over with a reset. */
  attached: (terminal: TerminalInfo, resumed: boolean) => void;
  /** A clean screen: everything kept follows, up to `replayed`. */
  reset: () => void;
  /** The replay is over: what comes next is live. */
  replayed: () => void;
  output: (data: Uint8Array) => void;
  marker: (event: MarkerEvent) => void;
  state: (terminal: TerminalInfo) => void;
  /** The core does not have this terminal (any more). */
  gone: (terminal: TerminalInfo | null) => void;
}

export interface Channel {
  /** This page is the one typing into the terminal now. */
  focus: () => void;
  input: (data: string) => void;
  inputBinary: (data: string) => void;
  resize: (cols: number, rows: number) => void;
  pause: () => void;
  resume: () => void;
  close: () => void;
  detach: () => void;
}

interface Open {
  id: string;
  handlers: ChannelHandlers;
  lastSeq: number | null;
  cols: number;
  rows: number;
  paused: boolean;
  focused: boolean;
}

type Listener = (connected: boolean) => void;

const encoder = new TextEncoder();

class Socket {
  private ws: WebSocket | null = null;
  private channels = new Map<number, Open>();
  private nextChannel = 1;
  private retry = 0;
  private timer: number | undefined;
  private listeners = new Set<Listener>();
  connected = false;

  onConnection(listener: Listener): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  attach(id: string, cols: number, rows: number,
    handlers: ChannelHandlers): Channel {
    const ch = this.nextChannel++;
    const open: Open = { id, handlers, lastSeq: null, cols, rows,
      paused: false, focused: false };
    this.channels.set(ch, open);
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.sendAttach(ch, open);
    } else {
      this.connect();
    }
    return {
      focus: () => {
        open.focused = true;
        this.json({ op: "focus", ch });
      },
      input: (data) => this.sendInput(ch, encoder.encode(data)),
      inputBinary: (data) => {
        const bytes = new Uint8Array(data.length);
        for (let i = 0; i < data.length; i++) bytes[i] = data.charCodeAt(i) & 255;
        this.sendInput(ch, bytes);
      },
      resize: (c, r) => {
        open.cols = c;
        open.rows = r;
        this.json({ op: "resize", ch, cols: c, rows: r });
      },
      pause: () => {
        if (open.paused) return;
        open.paused = true;
        this.json({ op: "pause", ch });
      },
      resume: () => {
        if (!open.paused) return;
        open.paused = false;
        this.json({ op: "resume", ch });
      },
      close: () => this.json({ op: "close", ch }),
      detach: () => {
        this.channels.delete(ch);
        this.json({ op: "detach", ch });
      },
    };
  }

  private connect(): void {
    if (this.ws) return;
    window.clearTimeout(this.timer);
    const scheme = location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(`${scheme}://${location.host}/api/ws`);
    ws.binaryType = "arraybuffer";
    this.ws = ws;
    ws.onopen = () => {
      this.retry = 0;
      this.setConnected(true);
      for (const [ch, open] of this.channels) {
        open.paused = false;
        this.sendAttach(ch, open);
      }
    };
    ws.onmessage = (event) => this.receive(event.data);
    ws.onclose = () => {
      this.ws = null;
      this.setConnected(false);
      if (this.channels.size === 0) return;
      const wait = RETRY_MS[Math.min(this.retry, RETRY_MS.length - 1)];
      this.retry += 1;
      this.timer = window.setTimeout(() => this.connect(), wait);
    };
  }

  private setConnected(value: boolean): void {
    if (this.connected === value) return;
    this.connected = value;
    for (const listener of this.listeners) listener(value);
  }

  private sendAttach(ch: number, open: Open): void {
    this.json({ op: "attach", ch, id: open.id, after: open.lastSeq,
      cols: open.cols, rows: open.rows,
      focus: open.focused && document.hasFocus() });
  }

  private json(message: object): void {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(message));
    }
  }

  private sendInput(ch: number, data: Uint8Array): void {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) return;
    const frame = new Uint8Array(5 + data.length);
    frame[0] = INPUT;
    new DataView(frame.buffer).setUint32(1, ch);
    frame.set(data, 5);
    this.ws.send(frame);
  }

  private receive(data: ArrayBuffer | string): void {
    if (typeof data !== "string") {
      const view = new DataView(data);
      if (data.byteLength < 13 || view.getUint8(0) !== OUTPUT) return;
      const open = this.channels.get(view.getUint32(1));
      if (!open) return;
      open.lastSeq = Number(view.getBigUint64(5));
      open.handlers.output(new Uint8Array(data, 13));
      return;
    }
    let msg: { op: string; ch?: number; [key: string]: unknown };
    try {
      msg = JSON.parse(data);
    } catch {
      return;
    }
    const open = msg.ch !== undefined ? this.channels.get(msg.ch) : undefined;
    if (!open) return;
    const h = open.handlers;
    switch (msg.op) {
      case "attached":
        h.attached(msg.terminal as TerminalInfo, Boolean(msg.resumed));
        break;
      case "reset":
        open.lastSeq = msg.seq as number;
        h.reset();
        break;
      case "replayed":
        h.replayed();
        break;
      case "ev":
        open.lastSeq = msg.seq as number;
        h.marker(msg as unknown as MarkerEvent);
        break;
      case "state":
        h.state(msg.terminal as TerminalInfo);
        break;
      case "gone":
        this.channels.delete(msg.ch as number);
        h.gone((msg.terminal as TerminalInfo | null) ?? null);
        break;
    }
  }
}

/** The one socket of this page. */
export const socket = new Socket();
