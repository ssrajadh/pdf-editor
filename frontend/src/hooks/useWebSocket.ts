import { useEffect, useRef, useCallback, useState } from "react";
import type { EditProgress, EditResult } from "../types";

interface Handlers {
  onProgress?: (progress: EditProgress) => void;
  onComplete?: (result: EditResult) => void;
  onError?: (message: string) => void;
}

const MAX_RETRIES = 5;
const BASE_DELAY = 1000;
const MAX_DELAY = 30000;

export function useWebSocket(sessionId: string | null, handlers: Handlers) {
  const wsRef = useRef<WebSocket | null>(null);
  const retriesRef = useRef(0);
  const unmountedRef = useRef(false);
  const handlersRef = useRef(handlers);
  handlersRef.current = handlers;

  const [isConnected, setIsConnected] = useState(false);
  const [isEditing, setIsEditing] = useState(false);
  const [isReconnecting, setIsReconnecting] = useState(false);

  const connect = useCallback(() => {
    if (!sessionId || unmountedRef.current) return;

    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const ws = new WebSocket(
      `${protocol}//${window.location.host}/api/edit/ws/${sessionId}`,
    );
    wsRef.current = ws;

    ws.onopen = () => {
      setIsConnected(true);
      setIsReconnecting(false);
      retriesRef.current = 0;
    };

    ws.onclose = () => {
      setIsConnected(false);
      wsRef.current = null;

      if (unmountedRef.current) return;

      if (retriesRef.current < MAX_RETRIES) {
        setIsReconnecting(true);
        const delay = Math.min(BASE_DELAY * Math.pow(2, retriesRef.current), MAX_DELAY);
        retriesRef.current += 1;
        setTimeout(connect, delay);
      }
    };

    ws.onerror = () => { /* onclose fires next */ };

    ws.onmessage = (event) => {
      let msg: Record<string, unknown>;
      try {
        msg = JSON.parse(event.data);
      } catch {
        return;
      }

      const h = handlersRef.current;

      switch (msg.type) {
        case "progress":
          h.onProgress?.({
            stage: msg.stage as string,
            message: msg.message as string,
            timestamp: msg.timestamp as string,
          });
          break;

        case "complete":
          setIsEditing(false);
          h.onComplete?.(msg.result as EditResult);
          break;

        case "error":
          setIsEditing(false);
          h.onError?.(msg.message as string);
          break;
      }
    };
  }, [sessionId]);

  useEffect(() => {
    unmountedRef.current = false;
    connect();
    return () => {
      unmountedRef.current = true;
      wsRef.current?.close();
    };
  }, [connect]);

  const sendEdit = useCallback((pageNum: number, prompt: string) => {
    const ws = wsRef.current;
    if (!ws || ws.readyState !== WebSocket.OPEN) return;

    setIsEditing(true);
    ws.send(JSON.stringify({ type: "edit", page_num: pageNum, prompt }));
  }, []);

  return { sendEdit, isConnected, isEditing, isReconnecting };
}
