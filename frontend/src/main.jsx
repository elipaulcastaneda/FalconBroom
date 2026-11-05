import React from "react"
import { createRoot } from "react-dom/client"
import "./styles.css"

const rootEl = document.getElementById("root")

function showStartupError(err) {
  const text = String((err && (err.stack || err.message)) || err)
  rootEl.innerHTML = ""
  const box = document.createElement("div")
  box.style.cssText = "max-width:720px;margin:12vh auto;padding:24px;color:#e2e8f0;font-family:system-ui,sans-serif"
  const title = document.createElement("h2")
  title.textContent = "FalconBroom could not start"
  const note = document.createElement("p")
  note.textContent = "Please send this message to support:"
  const pre = document.createElement("pre")
  pre.style.cssText = "white-space:pre-wrap;background:#0b1220;padding:12px;border-radius:8px;font-size:12px"
  pre.textContent = text
  box.append(title, note, pre)
  rootEl.appendChild(box)
}

class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props)
    this.state = { error: null }
  }
  static getDerivedStateFromError(error) {
    return { error }
  }
  componentDidCatch(error) {
    console.error(error)
  }
  render() {
    if (this.state.error) {
      return (
        <div style={{ maxWidth: 720, margin: "12vh auto", padding: 24, color: "#e2e8f0", fontFamily: "system-ui, sans-serif" }}>
          <h2>FalconBroom hit an error</h2>
          <p>Please send this message to support:</p>
          <pre style={{ whiteSpace: "pre-wrap", background: "#0b1220", padding: 12, borderRadius: 8, fontSize: 12 }}>
            {String(this.state.error.stack || this.state.error.message || this.state.error)}
          </pre>
        </div>
      )
    }
    return this.props.children
  }
}

window.addEventListener("error", (e) => {
  if (!rootEl.hasChildNodes()) showStartupError(e.error || e.message)
})

// App is imported dynamically so an error while loading its modules is shown instead of a blank window.
import("./App")
  .then(({ default: App }) => {
    createRoot(rootEl).render(
      <ErrorBoundary>
        <App />
      </ErrorBoundary>,
    )
  })
  .catch(showStartupError)
