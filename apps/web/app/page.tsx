export default function Home() {
  return (
    <div className="workspace">
      <aside aria-label="Workspace">
        <a className="brand" href="/" aria-label="Company Brain home">
          <span className="brand-mark" aria-hidden="true">cb</span>
          <span>Company Brain</span>
        </a>
        <div className="workspace-label">WORKSPACE</div>
        <nav aria-label="Main navigation"><a href="/" aria-current="page"><span aria-hidden="true">◈</span> Overview</a></nav>
        <div className="sidebar-note">A place for your team’s intelligence.</div>
      </aside>
      <main>
        <header><span>Workspace / Overview</span><span className="edition">Hackathon · 2026</span></header>
        <section className="intro" aria-labelledby="page-title">
          <p className="eyebrow">THE COMPANY BRAIN</p>
          <h1 id="page-title">Room for what’s next.</h1>
          <p className="subtitle">Your workspace starts here. Bring your agents together as you build.</p>
        </section>
        <section className="agents" aria-labelledby="agents-title">
          <div className="section-heading"><h2 id="agents-title">Agents</h2><span>0 configured</span></div>
          <div className="empty-state">
            <div className="orbit" aria-hidden="true"><span /><i /></div>
            <h3>No agents configured</h3>
            <p>When your team adds its first agent,<br />it will have a home here.</p>
          </div>
        </section>
        <footer>Company Brain <span>Foundation / 01</span></footer>
      </main>
    </div>
  );
}
