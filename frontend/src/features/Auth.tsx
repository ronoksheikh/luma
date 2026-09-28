import { Button, Card, Form, Spinner, Tabs } from "@heroui/react";
import { useState } from "react";
import { api, type User } from "../lib/api";
import { useStore } from "../lib/store";
import { Field, Logo, SecretField } from "../ui/kit";

export function AuthScreen() {
  const { signupOpen, setUser } = useStore();
  const [mode, setMode] = useState<"login" | "signup">("login");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    setBusy(true);
    try {
      const r = await api<{ user: User }>(`/api/auth/${mode}`, { method: "POST", json: { username: username.trim(), password } });
      setUser(r.user);
    } catch (err: any) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="relative flex min-h-full items-center justify-center overflow-hidden px-4 py-16">
      <div aria-hidden className="pointer-events-none absolute -top-48 left-1/2 h-[520px] w-[820px] -translate-x-1/2 rounded-full opacity-[0.10] blur-3xl" style={{ background: "var(--brand-gradient)" }} />
      <div className="relative w-full max-w-[380px]">
        <div className="mb-8 flex flex-col items-center gap-4 text-center">
          <Logo size={44} />
          <div>
            <h1 className="text-[26px] leading-tight">Luma Studio</h1>
            <p className="mt-1 text-sm text-muted">Motion graphics and video, directed by AI.</p>
          </div>
        </div>
        <Card className="p-1">
          <Card.Content className="p-5">
            {signupOpen && (
              <Tabs selectedKey={mode} onSelectionChange={(k) => { setMode(k as "login" | "signup"); setError(""); }} className="mb-5">
                <Tabs.ListContainer>
                  <Tabs.List aria-label="Account" className="w-full">
                    <Tabs.Tab id="login" className="flex-1">Sign in<Tabs.Indicator /></Tabs.Tab>
                    <Tabs.Tab id="signup" className="flex-1">Create account<Tabs.Indicator /></Tabs.Tab>
                  </Tabs.List>
                </Tabs.ListContainer>
              </Tabs>
            )}
            <Form onSubmit={submit} className="flex flex-col gap-4">
              <Field label="Username" value={username} onChange={setUsername} name="username" autoComplete="username" autoFocus isRequired />
              <SecretField label="Password" value={password} onChange={setPassword} name="password"
                autoComplete={mode === "login" ? "current-password" : "new-password"}
                description={mode === "signup" ? "At least 8 characters." : undefined} />
              {error && <p role="alert" className="text-sm text-danger">{error}</p>}
              <Button type="submit" fullWidth isPending={busy} isDisabled={!username || password.length < (mode === "signup" ? 8 : 1)}>
                {({ isPending }) => (
                  <>
                    {isPending && <Spinner color="current" size="sm" />}
                    {mode === "login" ? "Sign in" : "Create account"}
                  </>
                )}
              </Button>
            </Form>
          </Card.Content>
        </Card>
        <p className="mt-6 text-center text-xs text-muted">
          Your projects, renders and saved API keys stay with your account on this server.
        </p>
      </div>
    </div>
  );
}
