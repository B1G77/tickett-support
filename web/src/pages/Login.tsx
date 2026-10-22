import { useState, type FormEvent } from "react";
import { useMe } from "../auth";
import Logo from "../layout/Logo";

export default function Login() {
  const { login } = useMe();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      await login(username.trim(), password);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Falhou.");
      setPassword("");
      setBusy(false);
    }
  }

  return (
    <div className="login-page">
      <form className="login-card" onSubmit={submit} noValidate>
        <Logo className="login-logo" />
        <h1 className="login-title">Apoio Tickett</h1>
        <p className="login-hint">Entre com a sua conta para ver os rascunhos e os emails por responder.</p>
        <label className="field">
          <span className="field-label">Utilizador</span>
          <input className="input" name="username" autoComplete="username" autoCapitalize="none"
            spellCheck={false} required value={username} onChange={(e) => setUsername(e.target.value)} />
        </label>
        <label className="field">
          <span className="field-label">Palavra-passe</span>
          <input className="input" name="password" type="password" autoComplete="current-password" required
            value={password} onChange={(e) => setPassword(e.target.value)} />
        </label>
        {error && <p className="form-error" role="alert">{error}</p>}
        <button className="btn btn-primary btn-block" type="submit" disabled={busy || !username || !password}>
          {busy ? "A entrar…" : "Entrar"}
        </button>
      </form>
    </div>
  );
}
