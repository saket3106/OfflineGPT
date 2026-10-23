import React, { useEffect, useState } from "react";
import { Dialog } from "@base-ui/react/dialog";
import { FolderOpen, ShieldCheck, Square, CheckCircle2, AlertCircle, Clock, ChevronDown, ChevronRight, Server, Cpu, FileText, Database, HardDrive, Terminal } from "lucide-react";

export function MCPServersManagerPanel({ bridge, onClose }) {
  const [servers, setServers] = useState([]);
  const [loading, setLoading] = useState(true);
  const [expandedServer, setExpandedServer] = useState(null);

  const loadServers = async () => {
    setLoading(true);
    try {
      if (bridge?.getMCPServers) {
        const raw = await bridge.getMCPServers();
        const data = typeof raw === "string" ? JSON.parse(raw) : raw;
        if (data?.ok && Array.isArray(data.servers)) {
          setServers(data.servers);
          if (data.servers.length > 0 && !expandedServer) {
            setExpandedServer(data.servers[0].server.name);
          }
        }
      }
    } catch (err) {
      console.error("Failed to load MCP servers:", err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadServers();
  }, [bridge]);

  const toggleTool = async (toolName, currentEnabled) => {
    try {
      if (bridge?.toggleMCPTool) {
        await bridge.toggleMCPTool(toolName, !currentEnabled);
        await loadServers();
      }
    } catch (err) {
      console.error("Failed to toggle MCP tool:", err);
    }
  };

  const getServerIcon = (name) => {
    if (name.includes("file")) return <HardDrive size={15} />;
    if (name.includes("document") || name.includes("rag")) return <FileText size={15} />;
    if (name.includes("shell") || name.includes("system")) return <Terminal size={15} />;
    if (name.includes("memory")) return <Database size={15} />;
    return <Server size={15} />;
  };

  return (
    <div className="mcp-manager-container" aria-label="Model Context Protocol (MCP) Manager">
      <div className="mcp-manager-header">
        <div className="mcp-title-wrap">
          <Server size={18} className="mcp-primary-icon" />
          <div>
            <h3>Model Context Protocol (MCP) Manager</h3>
            <p className="mcp-subtitle">Inspect connected local MCP servers, configure tool permissions, and observe runtime capabilities.</p>
          </div>
        </div>
        {onClose && (
          <button type="button" className="mcp-close-btn" onClick={onClose}>
            Close
          </button>
        )}
      </div>

      {loading ? (
        <div className="mcp-loading-state">Loading registered MCP servers...</div>
      ) : (
        <div className="mcp-servers-grid">
          {servers.map(({ server, tools }) => {
            const isExpanded = expandedServer === server.name;
            const activeCount = tools.filter(t => t.enabled).length;
            return (
              <div key={server.name} className={"mcp-server-card " + (isExpanded ? "expanded" : "")}>
                <div className="mcp-server-card-header" onClick={() => setExpandedServer(isExpanded ? null : server.name)}>
                  <div className="mcp-server-info">
                    <span className="mcp-server-icon">{getServerIcon(server.name)}</span>
                    <div>
                      <span className="mcp-server-name">{server.name}</span>
                      <span className="mcp-server-badge">{server.kind}</span>
                    </div>
                  </div>
                  <div className="mcp-server-meta">
                    <span className="mcp-tool-count-badge">
                      {activeCount}/{tools.length} tools active
                    </span>
                    {isExpanded ? <ChevronDown size={15} /> : <ChevronRight size={15} />}
                  </div>
                </div>

                <p className="mcp-server-desc">{server.description}</p>

                {isExpanded && (
                  <div className="mcp-tools-list">
                    <h4>Available Tools</h4>
                    <div className="mcp-tools-table">
                      {tools.map(tool => (
                        <div key={tool.name} className={"mcp-tool-row " + (tool.enabled ? "enabled" : "disabled")}>
                          <div className="mcp-tool-details">
                            <span className="mcp-tool-name">{tool.name}</span>
                            {tool.is_write && <span className="mcp-perm-badge write">Approval required</span>}
                            {tool.is_host && <span className="mcp-perm-badge host">Host specialist</span>}
                          </div>
                          <label className="mcp-toggle-switch">
                            <input
                              type="checkbox"
                              checked={tool.enabled}
                              onChange={() => toggleTool(tool.name, tool.enabled)}
                            />
                            <span className="mcp-toggle-slider" />
                          </label>
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

export function AgentExecutionTraceView({ traces = [] }) {
  const [openIndices, setOpenIndices] = useState({});

  if (!traces || traces.length === 0) return null;

  const toggle = (idx) => {
    setOpenIndices(prev => ({ ...prev, [idx]: !prev[idx] }));
  };

  return (
    <div className="agent-trace-container" aria-label="Agentic Execution Trace">
      <div className="agent-trace-header">
        <Cpu size={14} />
        <span>Agentic Execution Trace ({traces.length} MCP step{traces.length > 1 ? "s" : ""})</span>
      </div>
      <div className="agent-trace-steps">
        {traces.map((trace, idx) => {
          const isOpen = !!openIndices[idx];
          const isError = trace.status === "error";
          return (
            <div key={idx} className={"agent-trace-step " + (isError ? "trace-error" : "trace-success")}>
              <div className="trace-step-summary" onClick={() => toggle(idx)}>
                <div className="trace-step-left">
                  {isError ? (
                    <AlertCircle size={14} className="trace-status-icon error" />
                  ) : (
                    <CheckCircle2 size={14} className="trace-status-icon success" />
                  )}
                  <span className="trace-server-tag">{trace.mcp_server || "mcp"}</span>
                  <span className="trace-tool-tag">{trace.tool}</span>
                </div>
                <div className="trace-step-right">
                  {trace.duration_ms != null && (
                    <span className="trace-duration">
                      <Clock size={11} />
                      {trace.duration_ms}ms
                    </span>
                  )}
                  {isOpen ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
                </div>
              </div>

              {isOpen && (
                <div className="trace-step-details">
                  {trace.input_args && Object.keys(trace.input_args).length > 0 && (
                    <div className="trace-payload-section">
                      <span className="trace-section-title">Arguments</span>
                      <pre className="trace-json-payload">{JSON.stringify(trace.input_args, null, 2)}</pre>
                    </div>
                  )}
                  <div className="trace-payload-section">
                    <span className="trace-section-title">Observation Output</span>
                    <pre className="trace-json-payload">
                      {typeof trace.output === "object" ? JSON.stringify(trace.output, null, 2) : String(trace.output || "")}
                    </pre>
                  </div>
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

export function AgentWorkspacePanel({ bridge, chatId, info, onInfo, request, error, onAttach, onOpenMcpManager }) {
  const [events, setEvents] = useState([]);
  const [approval, setApproval] = useState(null);
  const [history, setHistory] = useState([]);
  const [artifacts, setArtifacts] = useState([]);
  const [inspection, setInspection] = useState(null);
  const [feedback, setFeedback] = useState("");
  const [deciding, setDeciding] = useState(false);
  const [workspacePath, setWorkspacePath] = useState("");
  const [previews, setPreviews] = useState([]);
  const [attachmentPath, setAttachmentPath] = useState("");
  const [liveTraces, setLiveTraces] = useState([]);
  const latest = events[events.length - 1];

  useEffect(() => {
    let alive = true;
    setEvents([]); setApproval(null); setFeedback(""); setArtifacts([]); setInspection(null); setPreviews([]); setLiveTraces([]);
    request("getAgentHistory").then(result => { if (alive && Array.isArray(result)) setHistory(result); });
    const updateArtifacts = result => {
      if (!alive) return;
      if (Array.isArray(result)) setArtifacts(result);
      else if (result?.error) setFeedback(result.error);
    };
    request("getAgentArtifacts").then(updateArtifacts);
    const onEvent = raw => {
      let event; try { event = JSON.parse(raw); } catch { return; }
      if (event.type !== "agentStatus" || (event.chatId && event.chatId !== chatId)) return;
      setEvents(previous => [...previous.filter(item => item.taskId === event.taskId), event].slice(-32));
      if (Array.isArray(event.previews)) setPreviews(event.previews);
      if (Array.isArray(event.traces)) setLiveTraces(event.traces);
      setApproval(event.state === "AWAITING_APPROVAL" ? { ...event.approval, taskId: event.taskId } : null);
      if (["COMPLETED", "ANSWERED", "PARTIAL", "FAILED", "CANCELED", "BUDGET_EXHAUSTED"].includes(event.state)) {
        request("getAgentStatus").then(onInfo);
        request("getAgentHistory").then(result => { if (Array.isArray(result)) setHistory(result); });
        request("getAgentArtifacts").then(updateArtifacts);
      }
    };
    bridge?.event?.connect(onEvent);
    return () => { alive = false; bridge?.event?.disconnect(onEvent); };
  }, [bridge, chatId]);

  const decide = async allow => {
    if (!approval || deciding) return;
    setDeciding(true);
    try {
      const result = await request("approveAgent", approval.taskId, approval.requestId, approval.digest, allow);
      if (!result?.ok) setFeedback("This approval expired or changed. No operation was authorized.");
      setApproval(null);
    } catch { setFeedback("Approval could not be delivered. The operation remains blocked."); }
    finally { setDeciding(false); }
  };

  return (
    <section className="agent-workspace-panel" aria-label="Local Agent workspace">
      <div className="agent-workspace-heading">
        <strong><ShieldCheck size={15}/> Local Agent</strong>
        <span>Autonomous ReAct & MCP Tool-Calling</span>
        <div style={{ display: "flex", gap: "6px" }}>
          <button type="button" className="mcp-manage-btn" onClick={onOpenMcpManager} title="Configure MCP Servers and Tools">
            <Server size={13}/> MCP Tools
          </button>
          <button type="button" disabled={!!info?.active} onClick={async () => {
            const result = await request("pickAgentWorkspace");
            if (result?.ok) { onInfo({ ...info, workspace: result.workspace }); setFeedback(""); }
            else if (!result?.cancelled) setFeedback(result?.error || "Workspace selection is unavailable.");
          }}><FolderOpen size={14}/>{info?.workspace ? "Change workspace" : "Choose workspace"}</button>
        </div>
      </div>
      {info?.workspace && <p className="agent-workspace-path">{info.workspace}</p>}
      <form className="setting-input-row" onSubmit={async event => {
        event.preventDefault(); const result = await request("authorizeAgentWorkspace", workspacePath);
        if (result?.ok) { onInfo({...info,workspace:result.workspace});setFeedback("Only this folder is authorized. Writes still require approval."); }
        else setFeedback(result?.error || "Folder could not be authorized.");
      }}>
        <label htmlFor="agent-workspace-path">Workspace folder</label>
        <input id="agent-workspace-path" aria-label="Workspace folder" value={workspacePath} disabled={!!info?.active} onChange={event => setWorkspacePath(event.target.value)} placeholder="Paste an exact local folder path"/>
        <button type="submit" disabled={!!info?.active || !workspacePath.trim()}>Authorize this folder</button>
      </form>
      <small>Attached files belong to this conversation. Workspace authorization applies only to the folder you choose and is cleared on restart.</small>
      <details><summary>Attach a file by path</summary><form className="setting-input-row" onSubmit={async event => {
        event.preventDefault();const result=await request("authorizeAgentAttachment",attachmentPath);
        if(result?.ok){onAttach?.(result);setAttachmentPath("");setFeedback("This file is attached to your next message. Its containing folder was not authorized.");}
        else setFeedback(result?.error || "File could not be attached.");
      }}><label htmlFor="agent-attachment-path">Attachment file</label><input id="agent-attachment-path" aria-label="Attachment file" value={attachmentPath} onChange={event=>setAttachmentPath(event.target.value)} disabled={!!info?.active}/><button type="submit" disabled={!!info?.active || !attachmentPath.trim()}>Attach this file</button></form></details>
      {!info?.available && <p role="status">{info?.reason || "Checking local agent dependencies..."}</p>}
      <small>Existing files are preserved. Edits create approved copies. All actions flow through local MCP servers.</small>
      {(error || feedback) && <p className="agent-error" role="alert">{error || feedback}</p>}
      {latest && <div className="agent-current" role="status"><span>{latest.summary}</span><small>Step {latest.step || 0}/{latest.maxSteps || 8} • {latest.elapsedSeconds || 0}s</small>
        {!["COMPLETED", "ANSWERED", "PARTIAL", "FAILED", "CANCELED", "BUDGET_EXHAUSTED"].includes(latest.state) && <button onClick={() => bridge?.cancelAgent?.()}><Square size={12}/> Cancel</button>}
      </div>}
      {liveTraces.length > 0 && <AgentExecutionTraceView traces={liveTraces} />}
      {events.length > 0 && <details><summary>Task activity</summary><ol>{events.map((event,index) => <li key={index}>{event.summary}{event.failed ? " • failed" : ""}</li>)}</ol></details>}
      {previews.length > 0 && <div aria-label="Visually inspected images">{previews.map(item => <figure key={item.id}>
        <img src={item.thumbnail} alt={`Inspected source: ${item.name}`} width="160" style={{maxHeight:120,objectFit:"contain"}}/>
        <figcaption>{item.name} • Visually inspected</figcaption><button type="button" onClick={() => bridge?.openFile?.(item.path)}>Open original</button>
      </figure>)}</div>}
      {history.length > 0 && <details><summary>Recent agent tasks</summary><ul>{history.map(task => <li key={task.id}>{task.state} • {task.step} steps{task.outcome && <p>{task.outcome}</p>}</li>)}</ul></details>}
      {artifacts.length > 0 && <details className="agent-artifact-journal"><summary>Created files and recovery records ({artifacts.length})</summary>
        <p>Unconfirmed operations may have left a complete, partial or unavailable file. Inspection never retries, deletes or overwrites it.</p>
        <ul>{artifacts.map(item => <li key={item.id}><strong>{item.target}</strong> • {item.state === "VERIFIED" ? "Write acknowledged and verified" : "Interrupted or unconfirmed — review before retrying"}
          <p className="agent-workspace-path">{item.workspace}</p>
          {item.expectedSha256 && <small>Expected SHA-256: {item.expectedSha256}</small>}
          <button type="button" disabled={!!info?.active} onClick={async () => {
            const result = await request("inspectAgentArtifact", item.id);
            if (result?.ok) { setInspection(result); setFeedback(""); }
            else setFeedback(result?.error || "Artifact inspection is unavailable.");
          }}>Inspect without changing files</button>
        </li>)}</ul>
        {inspection && <div role="status"><strong>{inspection.target}: {inspection.inspectionStatus}</strong><p>{inspection.guidance}</p>{inspection.error && <p>{inspection.error}</p>}</div>}
      </details>}
      <Dialog.Root open={!!approval} onOpenChange={open => { if (!open) decide(false); }}>
        <Dialog.Portal><Dialog.Backdrop className="dialog-overlay"/><Dialog.Popup className="agent-approval-dialog">
          <Dialog.Title>Approve this file operation?</Dialog.Title>
          <Dialog.Description>Review the exact destination and proposed content. Approval applies once to this operation only.</Dialog.Description>
          <strong>{approval?.tool}</strong><p className="agent-workspace-path">{approval?.preview?.target}</p>
          <p>{approval?.preview?.effects}</p>
          <pre tabIndex={0}>{approval?.preview?.diff || approval?.preview?.content || "Create an empty directory."}</pre>
          <div className="agent-approval-actions"><button disabled={deciding} onClick={() => bridge?.cancelAgent?.()}>Cancel task</button><button disabled={deciding} onClick={() => decide(false)}>Deny</button><button className="primary-button" disabled={deciding} onClick={() => decide(true)}>Allow once</button></div>
        </Dialog.Popup></Dialog.Portal>
      </Dialog.Root>
    </section>
  );
}
