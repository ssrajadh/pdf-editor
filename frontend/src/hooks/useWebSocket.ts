import { useEffect, useRef, useState } from "react";

export function useWebSocket(url: string | null) {
  const wsRef = useRef<WebSocket | null>(null);
  const [lastMessage, setLastMessage] = useState<string | null>(null);
  const [isConnected, setIsConnected] = useState(false);

  useEffect(() => {
    if (!url) return;

    const ws = new WebSocket(url);
    wsRef.current = ws;

    ws.onopen = () => setIsConnected(true);
    ws.onclose = () => setIsConnected(false);
    ws.onmessage = (event) => setLastMessage(event.data);

    return () => {
      ws.close();
    };
  }, [url]);

  const send = (data: string) => {
    wsRef.current?.send(data);
  };

  return { send, lastMessage, isConnected };
}
