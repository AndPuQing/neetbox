import { BetterAtom } from "../utils/betterAtom";
import { addNotice } from "../utils/notification";
import { WEBSOCKET_URL } from "./api";
import { Project } from "./projects";
import { ImageMetadata } from "./types";

export interface WsMsgBase<Type extends string = string, Payload = undefined> {
  eventType: Type;
  name: string;
  payload: Payload;
  eventId: number;
  identityType: "web" | "cli";
  projectId: string;
  runId: string;
  timestamp: string;
  series?: string;
}

export type WsMsg =
  | WsMsgBase
  | (WsMsgBase<"handshake"> & { identityType: "web" | "cli" })
  | WsMsgBase<"action", { name: string; args: Record<string, string> }>
  | (WsMsgBase<"image"> & ImageMetadata)
  | WsMsgBase<"scalar", { series: string; x: number; y: number }>
  | WsMsgBase<
      "log",
      {
        message: string;
        series: string;
        whom: string;
      }
    >;

export class WsClient {
  ws!: WebSocket;
  nextId = ~~(Math.random() * 100000000) * 1000;
  callbacks = new Map<number, (msg: WsMsg) => void>();
  wsListeners = new Set<(msg: WsMsg) => void>();
  isReady = new BetterAtom(false);
  activeClose = false;

  constructor(readonly project: Project) {
    this.connect();
  }

  connect(reconnect = false) {
    this.ws = new WebSocket(WEBSOCKET_URL);
    this.ws.onopen = () => {
      console.info("ws open");
      this.send(
        {
          eventType: "handshake",
          identityType: "web",
        },
        (msg) => {
          console.info("ws joined", msg);
          this.isReady.value = true;
          if (reconnect) {
            addNotice({
              id: "ws-connection-state",
              type: "success",
              title: "WebSocket reconnected",
              content: `project "${this.project.nameOrId}"`,
            });
          }
        },
      );
    };
    this.ws.onmessage = (e) => {
      const json = JSON.parse(e.data) as WsMsg;
      // console.debug("ws receive", json);
      const eventId = json.eventId;
      const eventType = json.eventType;
      if (this.callbacks.has(eventId)) {
        this.callbacks.get(eventId)!(json);
        this.callbacks.delete(eventId);
      } else {
        if (eventType === "log") {
          this.project.handleLog({
            timestamp: json.timestamp,
            ...(json.payload as any),
          });
        }
        // console.warn("ws unhandled message", json);
        this.wsListeners.forEach((x) => x(json));
      }
    };
    this.ws.onclose = (e) => {
      this.isReady.value = false;
      if (!this.activeClose) {
        addNotice({
          id: "ws-connection-state",
          type: "error",
          title: "WebSocket disconnected",
          content: `project "${this.project.nameOrId}"`,
        });
        setTimeout(() => this.connect(true), 5000);
      }
    };
  }

  send(msg: Partial<WsMsg>, onReply?: (msg: WsMsg) => void) {
    const eventId = this.nextId++;
    const json = {
      ...msg,
      projectId: this.project.id,
      eventId: eventId,
    };
    console.info("ws send", json);
    this.ws.send(JSON.stringify(json));
    if (onReply) this.callbacks.set(eventId, onReply);
  }

  close() {
    this.activeClose = true;
    this.ws.close();
  }
}
