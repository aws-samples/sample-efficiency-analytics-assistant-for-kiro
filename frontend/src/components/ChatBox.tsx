import { useEffect, useRef, useState } from "react";
import { Avatar, Button, Card, Input, Space, Spin, Tag, Typography } from "antd";
import { RobotOutlined, SendOutlined, UserOutlined } from "@ant-design/icons";
import { api, ApiAuthError } from "../api/client";
import { login } from "../auth/auth";
import { useI18n } from "../i18n";

interface Msg {
  role: "user" | "agent";
  text: string;
}

/** NL Q&A chat box: calls /api/chat -> AgentCore Agent. */
export function ChatBox() {
  const { t } = useI18n();
  const SUGGESTIONS = [
    t("chat.suggest.1"),
    t("chat.suggest.2"),
    t("chat.suggest.3"),
    t("chat.suggest.4"),
  ];
  const [messages, setMessages] = useState<Msg[]>([
    { role: "agent", text: t("chat.greeting") },
  ]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [session, setSession] = useState<string | undefined>(undefined);
  const listRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    listRef.current?.scrollTo({ top: listRef.current.scrollHeight, behavior: "smooth" });
  }, [messages, loading]);

  const send = async (text: string) => {
    const q = text.trim();
    if (!q || loading) return;
    setInput("");
    setMessages((m) => [...m, { role: "user", text: q }]);
    setLoading(true);
    try {
      const res = await api.chat(q, session);
      setSession(res.session_id);
      setMessages((m) => [...m, { role: "agent", text: res.reply || t("chat.noAnswer") }]);
    } catch (e) {
      if (e instanceof ApiAuthError) {
        login();
        return;
      }
      setMessages((m) => [...m, { role: "agent", text: `${t("chat.error")}${String(e)}` }]);
    } finally {
      setLoading(false);
    }
  };

  return (
    <Card size="small" title={<span><RobotOutlined /> {t("chat.title")}</span>} style={{ height: "100%" }}
      styles={{ body: { display: "flex", flexDirection: "column", height: 360 } }}>
      <div ref={listRef} style={{ flex: 1, overflowY: "auto", paddingRight: 4 }}>
        <Space direction="vertical" size={12} style={{ width: "100%" }}>
          {messages.map((m, i) => (
            <div key={i} style={{ display: "flex", gap: 8, flexDirection: m.role === "user" ? "row-reverse" : "row" }}>
              <Avatar size="small" icon={m.role === "user" ? <UserOutlined /> : <RobotOutlined />}
                style={{ background: m.role === "user" ? "#3b82f6" : "#a855f7", flexShrink: 0 }} />
              <div style={{
                background: m.role === "user" ? "#1e3a5f" : "#16223c",
                padding: "8px 12px", borderRadius: 8, maxWidth: "82%",
                whiteSpace: "pre-wrap", wordBreak: "break-word", fontSize: 13,
              }}>
                <Typography.Text style={{ color: "#e2e8f0" }}>{m.text}</Typography.Text>
              </div>
            </div>
          ))}
          {loading && (
            <div style={{ display: "flex", gap: 8 }}>
              <Avatar size="small" icon={<RobotOutlined />} style={{ background: "#a855f7" }} />
              <Spin size="small" style={{ marginTop: 6 }} />
            </div>
          )}
        </Space>
      </div>

      {messages.length <= 1 && (
        <div style={{ margin: "8px 0", display: "flex", flexWrap: "wrap", gap: 6 }}>
          {SUGGESTIONS.map((s) => (
            <Tag key={s} style={{ cursor: "pointer" }} onClick={() => send(s)}>{s}</Tag>
          ))}
        </div>
      )}

      <Space.Compact style={{ marginTop: 8 }}>
        <Input
          placeholder={t("chat.placeholder")}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onPressEnter={() => send(input)}
          disabled={loading}
        />
        <Button type="primary" icon={<SendOutlined />} loading={loading} onClick={() => send(input)}>
          {t("chat.send")}
        </Button>
      </Space.Compact>
    </Card>
  );
}
