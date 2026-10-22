import { useState, useEffect, useMemo, useRef } from "react";
import { supabase } from "./supabase";

const CATEGORY_ORDER = ["produce","protein","dairy","grain","bakery","pantry","condiment","frozen","other"];
const CATEGORY_EMOJI = { produce:"🥬", protein:"🥩", dairy:"🧀", grain:"🌾", bakery:"🍞", pantry:"🫙", condiment:"🧴", frozen:"🧊", other:"📦" };

// week_start is a plain DATE string ("2026-06-01"); append a local midnight so it
// formats in the user's timezone instead of being parsed as UTC (off-by-one risk).
function formatWeek(weekStart, opts) {
  if (!weekStart) return "";
  return new Date(`${weekStart}T00:00:00`).toLocaleDateString("en-US", opts);
}

// The Monday the plan is filed under: this week's Mon–Fri, the upcoming Monday
// from Saturday on. Keep in sync with plan_monday() in agents/saturday_suggest.py.
function planMonday(today = new Date()) {
  const wd = (today.getDay() + 6) % 7;                      // Monday = 0
  const d = new Date(today.getFullYear(), today.getMonth(), today.getDate() + (wd < 5 ? -wd : 7 - wd));
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

// ─── Components ────────────────────────────────────────────────────────────

function EmptyState({ children }) {
  return (
    <div style={{ textAlign:"center", padding:"60px 20px",
      color:"#5a4030", fontSize:"14px", lineHeight:"1.6" }}>
      {children}
    </div>
  );
}

// Highlight search hits inside a name. Single characters are skipped — one
// letter matches nearly every recipe, and marking all of it is just noise.
function Highlight({ text, query }) {
  const words = (query || "").toLowerCase().split(/\s+/).filter(w => w.length > 1);
  if (!words.length) return text;
  const re = new RegExp(`(${words.map(w => w.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|")})`, "gi");
  const hit = new Set(words);
  return text.split(re).map((part, i) =>
    hit.has(part.toLowerCase())
      ? <mark key={i} style={{ background:"rgba(200,168,122,0.28)",
          color:"#f5ede0", borderRadius:"2px" }}>{part}</mark>
      : part
  );
}

// A tag rendered as a filter control. Same vocabulary as the chips in the
// Library filter bar, so clicking one in the list filters by it.
const TAG_PALETTE = {
  meal: { fg:"#f5ede0", bg:"rgba(245,237,224,0.09)", line:"rgba(245,237,224,0.2)",  on:"#f5ede0" },
  veg:  { fg:"#7ac87a", bg:"rgba(122,200,122,0.1)",  line:"rgba(122,200,122,0.22)", on:"#7ac87a" },
  tag:  { fg:"#c8a87a", bg:"rgba(200,168,122,0.1)",  line:"rgba(200,168,122,0.15)", on:"#c8a87a" },
};

function TagPill({ label, kind = "tag", on, onClick }) {
  const p = TAG_PALETTE[kind];
  return (
    <button onClick={onClick} aria-pressed={on} style={{
      fontSize:"10px", padding:"2px 8px", borderRadius:"20px", cursor:"pointer",
      fontFamily:"'Crimson Pro',Georgia,serif",
      color: on ? "#1c1206" : p.fg,
      background: on ? p.on : p.bg,
      border: `1px solid ${on ? p.on : p.line}`,
      fontWeight: on ? "600" : "400",
    }}>{label}</button>
  );
}

// onTag/isOn turn the tag row into filter controls (Library tab). Without
// them the card renders the old read-only two-tag summary.
function RecipeCard({ recipe, onSwap, onEdit, onRemove, onQueue, compact = false, onTag, isOn, query }) {
  const totalMins = (recipe.prep_time_mins || 0) + (recipe.cook_time_mins || 0);
  const interactive = !!onTag;
  return (
    <div style={{
      background: "rgba(255,252,245,0.06)",
      border: "1px solid rgba(200,180,140,0.15)",
      borderRadius: "12px",
      padding: compact ? "14px 16px" : "18px 20px",
      position: "relative",
      transition: "all 0.2s",
    }}>
      {recipe.in_queue && (
        <div style={{ position:"absolute", top:10, right:10, fontSize:"10px",
          background:"rgba(255,160,80,0.15)", color:"#ffaa55",
          border:"1px solid rgba(255,160,80,0.25)", padding:"2px 8px",
          borderRadius:"20px", letterSpacing:"0.08em" }}>QUEUED</div>
      )}
      {recipe.source_url && (
        <div style={{ position:"absolute", top: recipe.in_queue ? 32 : 10, right:10,
          fontSize:"10px", color:"#c8a87a" }}>
          {/instagram\.com/i.test(recipe.source_url)
            ? "📸 IG"
            : `🔗 ${(() => { try { return new URL(recipe.source_url).hostname.replace(/^www\./, ""); } catch { return "link"; } })()}`}
        </div>
      )}
	{/* Suppressed when interactive: vegetarian shows as a pill in the tag row */}
	{!interactive && (recipe.tags || []).includes("vegetarian") && (
          <div style={{ position:"absolute", top: recipe.in_queue ? 52 : 30, right:10,
            fontSize:"10px", color:"#7ac87a",
            background:"rgba(122,200,122,0.1)",
            border:"1px solid rgba(122,200,122,0.2)",
            padding:"2px 6px", borderRadius:"20px" }}>🌿 veg</div>
        )}
      <div style={{ fontFamily:"'Playfair Display', Georgia, serif",
        fontSize: compact ? "14px" : "16px", fontWeight:"700",
        color:"#f5ede0", marginBottom:"5px", paddingRight:"40px",
        lineHeight:"1.3", textWrap:"balance" }}>
        {query ? <Highlight text={recipe.name} query={query} /> : recipe.name}
      </div>
      {!compact && (
        <div style={{ fontSize:"12px", color:"#a08060", lineHeight:"1.6",
          marginBottom:"10px" }}>{recipe.description}</div>
      )}
      <div style={{ display:"flex", gap:"12px", alignItems:"center",
        flexWrap:"wrap" }}>
        {totalMins > 0 && (
          <span style={{ fontSize:"11px", color:"#806040" }}>⏱ {totalMins} min</span>
        )}
        {recipe.servings && (
          <span style={{ fontSize:"11px", color:"#806040" }}>👥 {recipe.servings}</span>
        )}
        {interactive ? (
          <>
            {/* meal type reads as a tag, not a section heading */}
            <TagPill label={recipe.meal_type} kind="meal"
              on={isOn(recipe.meal_type, "meal")}
              onClick={() => onTag(recipe.meal_type, "meal")} />
            {(recipe.tags || []).map(t => (
              <TagPill key={t} label={t} kind={t === "vegetarian" ? "veg" : "tag"}
                on={isOn(t, "tag")} onClick={() => onTag(t, "tag")} />
            ))}
          </>
        ) : (
          (recipe.tags || []).slice(0,2).map(t => (
            <span key={t} style={{ fontSize:"10px", color:"#c8a87a",
              background:"rgba(200,168,122,0.1)", padding:"2px 8px",
              borderRadius:"20px", border:"1px solid rgba(200,168,122,0.15)" }}>
              {t}
            </span>
          ))
        )}
        {onSwap && (
          <button onClick={() => onSwap(recipe)} style={{
            marginLeft:"auto", fontSize:"11px",
            background:"rgba(200,168,122,0.1)", color:"#c8a87a",
            border:"1px solid rgba(200,168,122,0.2)", padding:"4px 12px",
            borderRadius:"6px", cursor:"pointer"
          }}>swap ↕</button>
        )}
        {(onEdit || onRemove || onQueue) && (
          <div style={{ marginLeft:"auto", display:"flex", gap:"6px" }}>
            {[[onQueue && !recipe.in_queue ? onQueue : null, "add to queue", "Add to queue:"],
              [onEdit, "edit", "Edit"],
              [onRemove, "remove", "Remove from queue:"]].map(
              ([fn, text, aria]) => fn && (
                <button key={text} aria-label={`${aria} ${recipe.name}`}
                  onClick={() => fn(recipe)} style={{
                    fontSize:"11px", background:"rgba(200,168,122,0.1)", color:"#c8a87a",
                    border:"1px solid rgba(200,168,122,0.2)", padding:"4px 12px",
                    borderRadius:"6px", cursor:"pointer"
                  }}>{text}</button>
              ))}
          </div>
        )}
      </div>
    </div>
  );
}

function LibraryTab({ recipes, onEdit, onQueue }) {
  const [query, setQuery] = useState("");
  const [meals, setMeals] = useState([]);
  const [tags, setTags]   = useState([]);
  const [showRare, setShowRare] = useState(false);

  // Defensive: schema.sql §6c now normalises tags on write, so this should be
  // a no-op. Kept because it is one line and a mixed-case tag would otherwise
  // render as two separate chips for the same thing.
  const rows = useMemo(
    () => recipes.map(r => ({
      ...r, tags: [...new Set((r.tags || []).map(t => t.toLowerCase()))],
    })),
    [recipes]
  );

  const tagCount = useMemo(() => {
    const c = {};
    for (const r of rows) for (const t of r.tags) c[t] = (c[t] || 0) + 1;
    return c;
  }, [rows]);

  // Tags used more than once get a chip; the long tail hides behind an
  // expander so the bar stays scannable instead of becoming a wall.
  const common = useMemo(() => Object.keys(tagCount)
    .filter(t => tagCount[t] > 1)
    .sort((a, b) => tagCount[b] - tagCount[a] || a.localeCompare(b)), [tagCount]);
  const rare = useMemo(() => Object.keys(tagCount)
    .filter(t => tagCount[t] === 1).sort(), [tagCount]);

  // Meal types are OR'd, tags are AND'd. If everything were AND'd, selecting
  // both lunch and dinner would return nothing, since no recipe is both.
  const passes = (r, q, ms, ts) => {
    if (q) {
      // Name + tags only, deliberately not description — matching prose made
      // "cold" return recipes whose description merely said "warm or cold".
      const hay = `${r.name} ${r.tags.join(" ")} ${r.meal_type}`.toLowerCase();
      if (!q.toLowerCase().split(/\s+/).filter(Boolean).every(w => hay.includes(w))) return false;
    }
    if (ms.length && !ms.includes(r.meal_type)) return false;
    return ts.every(t => r.tags.includes(t));
  };

  const visible   = rows.filter(r => passes(r, query, meals, tags));
  const filtering = !!query || meals.length > 0 || tags.length > 0;

  const flip   = (list, v) => list.includes(v) ? list.filter(x => x !== v) : [...list, v];
  const isOn   = (v, kind) => (kind === "meal" ? meals : tags).includes(v);
  const onTag  = (v, kind) => kind === "meal" ? setMeals(flip(meals, v)) : setTags(flip(tags, v));
  const reset  = () => { setQuery(""); setMeals([]); setTags([]); };

  // The number on a chip is what you'd get if you toggled it — so a chip
  // reading 0 is a dead end, and gets disabled rather than letting you
  // click your way to an empty list.
  const wouldGive = (v, kind) => rows.filter(r => passes(
    r, query,
    kind === "meal" ? flip(meals, v) : meals,
    kind === "tag"  ? flip(tags,  v) : tags,
  )).length;

  const Chip = ({ value, kind }) => {
    const on = isOn(value, kind);
    const n = wouldGive(value, kind);
    const dead = !on && n === 0;
    const accent = kind === "meal" ? "#f5ede0" : value === "vegetarian" ? "#7ac87a" : "#c8a87a";
    return (
      <button onClick={() => onTag(value, kind)} disabled={dead} aria-pressed={on}
        style={{
          display:"inline-flex", alignItems:"baseline", gap:"6px",
          padding:"5px 11px", borderRadius:"20px",
          cursor: dead ? "not-allowed" : "pointer",
          opacity: dead ? 0.28 : 1,
          fontFamily:"'Crimson Pro',Georgia,serif", fontSize:"12px",
          fontWeight: on ? "600" : "400",
          color: on ? "#1c1206" : "#a08060",
          background: on ? accent : "transparent",
          border: `1px solid ${on ? accent : "rgba(200,168,122,0.18)"}`,
          transition:"all 0.15s",
        }}>
        {value}
        <span style={{ fontSize:"10px", fontVariantNumeric:"tabular-nums",
          color: on ? "rgba(28,18,6,0.55)" : "#5a4030" }}>{n}</span>
      </button>
    );
  };

  const linkBtn = {
    background:"none", border:"none", padding:"5px 4px", cursor:"pointer",
    fontFamily:"'Crimson Pro',Georgia,serif", fontSize:"12px", color:"#806040",
    textDecoration:"underline", textUnderlineOffset:"3px",
  };

  return (
    <div>
      <h2 style={{ fontFamily:"'Playfair Display',Georgia,serif",
        fontSize:"22px", color:"#f5ede0", marginBottom:"6px" }}>
        Recipe Library
      </h2>
      <p style={{ fontSize:"13px", color:"#806040", marginBottom:"4px" }}>
        Search by name or tag. Add new recipes from the Queue tab.
      </p>

      {/* Sticky, so the filters stay reachable while scrolling a long list */}
      <div style={{ position:"sticky", top:0, zIndex:5, background:"#120d07",
        padding:"16px 0 12px", borderBottom:"1px solid rgba(200,168,122,0.1)",
        display:"flex", flexDirection:"column", gap:"12px" }}>

        <div style={{ position:"relative", display:"flex", alignItems:"center" }}>
          <span style={{ position:"absolute", left:14, fontSize:"13px",
            color:"#806040", pointerEvents:"none" }}>⌕</span>
          <input
            value={query}
            onChange={e => setQuery(e.target.value)}
            placeholder="Search recipes and tags…"
            aria-label="Search recipes"
            style={{ width:"100%", padding:"12px 38px", borderRadius:"8px",
              background:"rgba(255,252,245,0.04)",
              border:"1px solid rgba(200,168,122,0.25)",
              fontFamily:"'Crimson Pro',Georgia,serif", fontSize:"15px",
              color:"#f5ede0", outline:"none" }}
          />
          {query && (
            <button onClick={() => setQuery("")} aria-label="Clear search"
              style={{ position:"absolute", right:10, background:"none",
                border:"none", cursor:"pointer", color:"#806040",
                fontSize:"15px", fontFamily:"'Crimson Pro',Georgia,serif" }}>×</button>
          )}
        </div>

        <div style={{ display:"flex", flexWrap:"wrap", gap:"6px", alignItems:"center" }}>
          {["lunch","dinner"].map(m => <Chip key={m} value={m} kind="meal" />)}
          <span style={{ width:"1px", alignSelf:"stretch", margin:"2px 5px",
            background:"rgba(200,168,122,0.2)" }} />
          {common.map(t => <Chip key={t} value={t} kind="tag" />)}
          {showRare && rare.map(t => <Chip key={t} value={t} kind="tag" />)}
          {rare.length > 0 && (
            <button onClick={() => setShowRare(!showRare)} style={linkBtn}>
              {showRare ? "fewer tags" : `+${rare.length} more tags`}
            </button>
          )}
        </div>

        <div style={{ display:"flex", justifyContent:"space-between",
          alignItems:"baseline", fontSize:"12px", color:"#806040" }}>
          <span>
            <b style={{ color:"#f5ede0", fontWeight:"500",
              fontVariantNumeric:"tabular-nums" }}>{visible.length}</b>
            {filtering ? ` of ${rows.length} recipes` : " recipes"}
          </span>
          {filtering && (
            <button onClick={reset} style={{ ...linkBtn, padding:0 }}>clear filters</button>
          )}
        </div>
      </div>

      {visible.length === 0 ? (
        <EmptyState>
          Nothing matches {[query, ...meals, ...tags].filter(Boolean).join(" · ")}.
          <br />Try dropping a tag, or add the recipe from the Queue tab.
        </EmptyState>
      ) : (
        <div style={{ display:"flex", flexDirection:"column", gap:"8px",
          padding:"16px 0 40px" }}>
          {visible.map(r => (
            <RecipeCard key={r.id} recipe={r} compact onEdit={onEdit} onQueue={onQueue}
              onTag={onTag} isOn={isOn} query={query} />
          ))}
        </div>
      )}
    </div>
  );
}

function ShoppingTab({ items, setItems, planId, weekLabel }) {
  const byCategory = {};
  for (const item of items) {
    const cat = item.category || "other";
    if (!byCategory[cat]) byCategory[cat] = [];
    byCategory[cat].push(item);
  }
  const total = items.length;
  const done  = items.filter(i => i.checked).length;
  const pct   = total ? Math.round((done / total) * 100) : 0;

  const toggle = async (name) => {
    const next = items.map(i => i.name === name ? {...i, checked: !i.checked} : i);
    setItems(next);   // optimistic
    if (planId) {
      await supabase.from("weekly_plans").update({ shopping_list: next }).eq("id", planId);
    }
  };

  if (!total) {
    return (
      <EmptyState>
        No shopping list yet — it's generated Sunday morning once the plan is set.
      </EmptyState>
    );
  }

  return (
    <div>
      <div style={{ display:"flex", justifyContent:"space-between",
        alignItems:"center", marginBottom:"24px" }}>
        <div>
          <h2 style={{ fontFamily:"'Playfair Display',Georgia,serif",
            fontSize:"24px", color:"#f5ede0", margin:"0 0 4px" }}>
            Shopping List
          </h2>
          <div style={{ fontSize:"12px", color:"#806040" }}>
            {done}/{total} items checked{weekLabel ? ` · ${weekLabel}` : ""}
          </div>
        </div>
        <div style={{
          width:"48px", height:"48px", borderRadius:"50%",
          background:`conic-gradient(#c8a87a ${pct * 3.6}deg, rgba(200,168,122,0.1) 0deg)`,
          display:"flex", alignItems:"center", justifyContent:"center",
          fontSize:"12px", color:"#c8a87a", fontWeight:"700"
        }}>{pct}%</div>
      </div>

      {CATEGORY_ORDER.map(cat => {
        const catItems = byCategory[cat];
        if (!catItems?.length) return null;
        return (
          <div key={cat} style={{ marginBottom:"20px" }}>
            <div style={{ fontSize:"11px", color:"#806040", letterSpacing:"0.1em",
              textTransform:"uppercase", marginBottom:"8px", display:"flex",
              alignItems:"center", gap:"8px" }}>
              <span>{CATEGORY_EMOJI[cat]}</span>
              <span>{cat}</span>
            </div>
            <div style={{ display:"flex", flexDirection:"column", gap:"2px" }}>
              {catItems.map(item => (
                <div key={item.name}
                  onClick={() => toggle(item.name)}
                  style={{
                    display:"flex", alignItems:"center", gap:"12px",
                    padding:"10px 14px", borderRadius:"8px", cursor:"pointer",
                    background: item.checked ? "rgba(200,168,122,0.04)" : "rgba(255,252,245,0.04)",
                    border: "1px solid rgba(200,168,122,0.08)",
                    opacity: item.checked ? 0.45 : 1,
                    transition:"all 0.15s",
                  }}>
                  <div style={{
                    width:"18px", height:"18px", borderRadius:"4px", flexShrink:0,
                    border: item.checked ? "none" : "1.5px solid rgba(200,168,122,0.3)",
                    background: item.checked ? "#c8a87a" : "transparent",
                    display:"flex", alignItems:"center", justifyContent:"center",
                    fontSize:"11px"
                  }}>{item.checked ? "✓" : ""}</div>
                  <div style={{ flex:1 }}>
                    <span style={{ fontSize:"13px", color:"#f5ede0",
                      textDecoration: item.checked ? "line-through" : "none" }}>
                      {item.name}
                    </span>
                    <span style={{ fontSize:"12px", color:"#806040", marginLeft:"10px" }}>
                      {item.amount}{item.unit ? ` ${item.unit}` : ""}
                    </span>
                  </div>
                  <div style={{ fontSize:"10px", color:"#5a4030" }}>
                    {(item.for_recipes || []).join(", ").slice(0, 30)}
                  </div>
                </div>
              ))}
            </div>
          </div>
        );
      })}
    </div>
  );
}

function SwapModal({ recipe, allRecipes, onConfirm, onClose }) {
  const [selected, setSelected] = useState(null);
  const options = allRecipes.filter(r =>
    r.meal_type === recipe.meal_type && r.id !== recipe.id
  );
  return (
    <div style={{
      position:"fixed", inset:0, background:"rgba(20,12,6,0.85)",
      display:"flex", alignItems:"center", justifyContent:"center",
      zIndex:100, padding:"20px"
    }}>
      <div style={{
        background:"#1e1408", border:"1px solid rgba(200,168,122,0.2)",
        borderRadius:"16px", padding:"28px", width:"100%", maxWidth:"520px",
        maxHeight:"80vh", overflowY:"auto"
      }}>
        <div style={{ fontFamily:"'Playfair Display',Georgia,serif",
          fontSize:"18px", color:"#f5ede0", marginBottom:"6px" }}>
          Swap "{recipe.name}"
        </div>
        <div style={{ fontSize:"12px", color:"#806040", marginBottom:"20px" }}>
          Choose a replacement {recipe.meal_type}
        </div>
        <div style={{ display:"flex", flexDirection:"column", gap:"8px", marginBottom:"20px" }}>
          {options.map(r => (
            <div key={r.id}
              onClick={() => setSelected(r)}
              style={{
                padding:"12px 16px", borderRadius:"10px", cursor:"pointer",
                border: selected?.id === r.id
                  ? "1px solid rgba(200,168,122,0.5)"
                  : "1px solid rgba(200,168,122,0.1)",
                background: selected?.id === r.id
                  ? "rgba(200,168,122,0.08)" : "rgba(255,252,245,0.03)",
              }}>
              <div style={{ display:"flex", alignItems:"center", gap:"10px" }}>
                {r.in_queue && <span style={{ fontSize:"10px", color:"#ffaa55" }}>★ QUEUED</span>}
                <span style={{ fontSize:"13px", color:"#f5ede0",
                  fontFamily:"'Playfair Display',Georgia,serif" }}>{r.name}</span>
              </div>
              <div style={{ fontSize:"11px", color:"#806040", marginTop:"3px" }}>
                {r.description?.slice(0,80)}...
              </div>
            </div>
          ))}
        </div>
        <div style={{ display:"flex", gap:"10px" }}>
          <button onClick={onClose} style={{
            flex:1, padding:"10px", background:"transparent",
            border:"1px solid rgba(200,168,122,0.2)", borderRadius:"8px",
            color:"#806040", cursor:"pointer", fontSize:"13px"
          }}>Cancel</button>
          <button onClick={() => selected && onConfirm(recipe, selected)}
            disabled={!selected}
            style={{
              flex:1, padding:"10px",
              background: selected ? "rgba(200,168,122,0.15)" : "rgba(200,168,122,0.05)",
              border:"1px solid rgba(200,168,122,0.3)", borderRadius:"8px",
              color: selected ? "#c8a87a" : "#5a4030",
              cursor: selected ? "pointer" : "not-allowed", fontSize:"13px",
              fontWeight:"600"
            }}>Confirm Swap</button>
        </div>
      </div>
    </div>
  );
}

// ─── Weekly servings: lunches / dinners / people ────────────────────────────

// Keep in sync with DEFAULT_SETTINGS in agents/saturday_suggest.py.
const DEFAULT_SETTINGS = {
  lunches: 5, dinners: 3, people: 2,
  vegetarian_lunches: false, vegetarian_dinners: true,
};
// A meal_settings row (or a plan's settings) over the defaults, ignoring other
// columns and any that aren't applied yet (schema.sql 6d / 6e).
const withDefaults = s => Object.fromEntries(
  Object.entries(DEFAULT_SETTINGS).map(([k, v]) => [k, s?.[k] ?? v]));
const VEGETARIAN_FIELDS = [
  { key:"vegetarian_lunches", meal:"lunch",  label:"Vegetarian lunches" },
  { key:"vegetarian_dinners", meal:"dinner", label:"Vegetarian dinners" },
];
// "lunch", "dinner", "lunch and dinner", or "" when neither is on.
const vegetarianFor = s =>
  VEGETARIAN_FIELDS.filter(f => s[f.key]).map(f => f.meal).join(" and ");
const SETTING_FIELDS = [
  { key:"lunches", label:"Lunches per week", chip:"lunches", hint:"Make-ahead, eaten Mon–Fri", min:0, max:14 },
  { key:"dinners", label:"Dinners per week", chip:"dinners", hint:"Cooked fresh, vegetarian",  min:0, max:14 },
  { key:"people",  label:"People eating",    chip:"people",  hint:"Everyone sharing these meals", min:1, max:8 },
];
const BUTTON_FONT = "'Crimson Pro',Georgia,serif";

// Recipes saved without a serving size count as 4 (same as the agent).
const servingsOf = r => r.servings || 4;
const totalServings = recipes => recipes.reduce((n, r) => n + servingsOf(r), 0);
const servingsNeeded = s => ({
  lunch: s.lunches * s.people,
  dinner: s.dinners * s.people,
  total: (s.lunches + s.dinners) * s.people,
});
const settingsLabel = s => `${s.lunches} · ${s.dinners} · ${s.people}`;

function Stepper({ field, value, onChange }) {
  const step = (delta, label) => {
    const next = value + delta;
    const off = next < field.min || next > field.max;
    return (
      <button aria-label={`${label} ${field.chip}`} disabled={off}
        onClick={() => onChange(next)} style={{
          width:"44px", height:"44px", borderRadius:"10px", fontSize:"22px", lineHeight:1,
          border:"1px solid rgba(200,168,122,0.3)", background:"rgba(200,168,122,0.08)",
          color:"#c8a87a", fontFamily:BUTTON_FONT,
          cursor: off ? "default" : "pointer", opacity: off ? 0.35 : 1,
        }}>{delta < 0 ? "−" : "+"}</button>
    );
  };
  return (
    <div style={{ display:"flex", alignItems:"center", gap:"6px", flexShrink:0 }}>
      {step(-1, "Fewer")}
      <div style={{ width:"44px", textAlign:"center", fontSize:"24px", fontWeight:"700",
        fontFamily:"'Playfair Display',Georgia,serif", color:"#f5ede0" }}>{value}</div>
      {step(1, "More")}
    </div>
  );
}

function Toggle({ on, label, onChange }) {
  return (
    <button role="switch" aria-checked={on} aria-label={label} onClick={() => onChange(!on)}
      style={{
        width:"52px", height:"32px", borderRadius:"16px", padding:"3px", flexShrink:0,
        border:"1px solid rgba(200,168,122,0.3)", cursor:"pointer", textAlign:"left",
        background: on ? "#c8a87a" : "rgba(200,168,122,0.08)",
      }}>
      <span style={{
        display:"block", width:"24px", height:"24px", borderRadius:"50%",
        background: on ? "#120d07" : "#a08560",
        transform: on ? "translateX(20px)" : "none", transition:"transform 0.15s",
      }} />
    </button>
  );
}

function SettingsTab({ defaults, onSaved }) {
  const [draft, setDraft] = useState(defaults);
  const [status, setStatus] = useState("");
  const need = servingsNeeded(draft);

  const save = async () => {
    setStatus("Saving…");
    const { error } = await supabase.from("meal_settings").update(draft).eq("id", 1);
    if (error) { setStatus(`Couldn't save: ${error.message}`); return; }
    onSaved(draft);
    setStatus("Saved");
    setTimeout(() => setStatus(""), 2500);
  };

  const block = { borderTop:"1px solid rgba(200,168,122,0.18)", paddingTop:"12px" };
  return (
    <div style={{ paddingBottom:"40px" }}>
      <h2 style={{ fontFamily:"'Playfair Display',Georgia,serif", fontSize:"20px",
        color:"#f5ede0", marginBottom:"4px" }}>Weekly defaults</h2>
      <p style={{ fontSize:"15px", lineHeight:"1.5", color:"#a08560", marginBottom:"16px" }}>
        Saturday's plan and the Generate button both use these. Change them any
        time; the current plan isn't touched until you regenerate.
      </p>

      <div style={{ display:"flex", flexDirection:"column", gap:"10px", marginBottom:"24px" }}>
        {SETTING_FIELDS.map(f => (
          <div key={f.key} style={{
            display:"flex", alignItems:"center", justifyContent:"space-between", gap:"16px",
            background:"#1c140c", border:"1px solid rgba(200,168,122,0.18)",
            borderRadius:"12px", padding:"14px 18px" }}>
            <div>
              <div style={{ fontSize:"17px", fontWeight:"500", color:"#f5ede0" }}>{f.label}</div>
              <div style={{ fontSize:"14px", color:"#a08560", marginTop:"2px" }}>{f.hint}</div>
            </div>
            <Stepper field={f} value={draft[f.key]}
              onChange={v => setDraft(d => ({ ...d, [f.key]: v }))} />
          </div>
        ))}
      </div>

      <div style={{ display:"flex", flexDirection:"column", gap:"10px", marginBottom:"24px" }}>
        {VEGETARIAN_FIELDS.map(f => (
          <div key={f.key} style={{
            display:"flex", alignItems:"center", justifyContent:"space-between", gap:"16px",
            background:"#1c140c", border:"1px solid rgba(200,168,122,0.18)",
            borderRadius:"12px", padding:"14px 18px" }}>
            <div>
              <div style={{ fontSize:"17px", fontWeight:"500", color:"#f5ede0" }}>{f.label}</div>
              <div style={{ fontSize:"14px", color:"#a08560", marginTop:"2px" }}>
                Only suggest {f.meal} recipes tagged vegetarian
              </div>
            </div>
            <Toggle on={draft[f.key]} label={f.label}
              onChange={v => setDraft(d => ({ ...d, [f.key]: v }))} />
          </div>
        ))}
      </div>

      <div style={{ background:"rgba(200,168,122,0.06)", border:"1px solid rgba(200,168,122,0.25)",
        borderRadius:"14px", padding:"20px 22px", marginBottom:"16px" }}>
        <div style={{ fontSize:"11px", letterSpacing:"0.16em", textTransform:"uppercase",
          color:"#a08560", marginBottom:"10px" }}>Servings to plan each week</div>
        <div style={{ display:"flex", alignItems:"baseline", gap:"14px", flexWrap:"wrap" }}>
          <div style={{ fontFamily:"'Playfair Display',Georgia,serif", fontSize:"44px",
            fontWeight:"700", color:"#f5ede0", lineHeight:1 }}>{need.total}</div>
          <div style={{ fontSize:"16px" }}>
            ({draft.lunches} lunches + {draft.dinners} dinners) × {draft.people} people
          </div>
        </div>
        <div style={{ display:"grid", gridTemplateColumns:"repeat(2, minmax(0, 1fr))",
          gap:"12px", marginTop:"18px" }}>
          {[["Lunches", need.lunch], ["Dinners", need.dinner]].map(([name, n]) => (
            <div key={name} style={block}>
              <div style={{ fontSize:"14px", color:"#a08560" }}>{name}</div>
              <div style={{ fontSize:"18px", color:"#f5ede0", fontWeight:"500" }}>{n} servings</div>
              <div style={{ fontSize:"14px" }}>about {Math.ceil(n / 4)} recipes</div>
            </div>
          ))}
        </div>
      </div>

      <p style={{ fontSize:"14px", lineHeight:"1.55", color:"#a08560", marginBottom:"24px" }}>
        Recipe counts assume 4 servings each, the default for any recipe without a
        serving size. Whole recipes only, so a plan usually lands a little over, and
        the extra servings are your leftovers.
      </p>

      <div style={{ display:"flex", gap:"10px", alignItems:"center" }}>
        <button onClick={save} style={{
          flex:1, height:"48px", borderRadius:"10px", border:"none", background:"#c8a87a",
          color:"#120d07", fontSize:"16px", fontWeight:"600", cursor:"pointer",
          fontFamily:BUTTON_FONT }}>Save defaults</button>
        <button onClick={() => setDraft(d => ({ ...d, ...DEFAULT_SETTINGS,
            vegetarian_lunches: d.vegetarian_lunches, vegetarian_dinners: d.vegetarian_dinners }))} style={{
          height:"48px", padding:"0 20px", borderRadius:"10px", background:"transparent",
          border:"1px solid rgba(200,168,122,0.3)", color:"#c8a87a", fontSize:"15px",
          cursor:"pointer", fontFamily:BUTTON_FONT }}>
          Reset to {settingsLabel(DEFAULT_SETTINGS)}
        </button>
      </div>
      {status && <div style={{ fontSize:"14px", color:"#a08560", marginTop:"12px" }}>{status}</div>}
    </div>
  );
}

// Opened by Generate / regenerate. The numbers are a one-off for this week —
// the backend stores them on the plan, never on the saved defaults.
function GenerateModal({ initial, defaults, weekLabel, generating, status, onGenerate, onClose }) {
  const [draft, setDraft] = useState(initial);
  const need = servingsNeeded(draft);
  const isDefault = SETTING_FIELDS.every(f => draft[f.key] === defaults[f.key]);
  const ghost = {
    height:"48px", padding:"0 20px", borderRadius:"10px", background:"transparent",
    border:"1px solid rgba(200,168,122,0.3)", color:"#c8a87a", fontSize:"15px",
    cursor:"pointer", fontFamily:BUTTON_FONT,
  };
  const primaryOff = generating || need.total === 0;
  const vegFor = vegetarianFor(defaults);

  return (
    <div style={{
      position:"fixed", inset:0, background:"rgba(8,5,2,0.72)", zIndex:100,
      display:"flex", alignItems:"center", justifyContent:"center", padding:"24px",
    }}>
      <div role="dialog" aria-modal="true" aria-labelledby="gen-title" style={{
        width:"100%", maxWidth:"480px", maxHeight:"100%", overflowY:"auto",
        background:"#1c140c", border:"1px solid rgba(200,168,122,0.3)", borderRadius:"16px",
        padding:"28px 28px 24px", boxShadow:"0 24px 60px rgba(0,0,0,0.55)",
      }}>
        <div style={{ fontSize:"11px", letterSpacing:"0.16em", textTransform:"uppercase",
          color:"#a08560", marginBottom:"8px" }}>{generating ? "Generating" : weekLabel}</div>
        <h2 id="gen-title" style={{ fontFamily:"'Playfair Display',Georgia,serif",
          fontSize:"26px", fontWeight:"700", color:"#f5ede0", lineHeight:"1.15",
          margin:"0 0 6px" }}>
          {generating ? "Planning your week" : "Plan this week"}
        </h2>
        <p style={{ fontSize:"15px", lineHeight:"1.5", color:"#a08560", margin:"0 0 16px" }}>
          {generating ? weekLabel : "Adjust for this week only. Your defaults in Settings don't change."}
        </p>

        {generating ? (
          <div style={{ display:"flex", gap:"8px", marginBottom:"14px" }}>
            {SETTING_FIELDS.map(f => (
              <div key={f.key} style={{ flex:1, textAlign:"center", padding:"10px 12px",
                border:"1px solid rgba(200,168,122,0.2)", borderRadius:"10px" }}>
                <div style={{ fontFamily:"'Playfair Display',Georgia,serif", fontSize:"24px",
                  fontWeight:"700", color:"#f5ede0", lineHeight:"1.1" }}>{draft[f.key]}</div>
                <div style={{ fontSize:"13px", color:"#a08560" }}>{f.chip}</div>
              </div>
            ))}
          </div>
        ) : (
          <div style={{ marginBottom:"16px", borderTop:"1px solid rgba(200,168,122,0.15)" }}>
            {SETTING_FIELDS.map(f => (
              <div key={f.key} style={{ display:"flex", alignItems:"center",
                justifyContent:"space-between", gap:"16px", padding:"10px 0",
                borderBottom:"1px solid rgba(200,168,122,0.15)" }}>
                <div style={{ fontSize:"17px", fontWeight:"500", color:"#f5ede0",
                  textTransform:"capitalize" }}>{f.chip}</div>
                <Stepper field={f} value={draft[f.key]}
                  onChange={v => setDraft(d => ({ ...d, [f.key]: v }))} />
              </div>
            ))}
          </div>
        )}

        <div style={{ background:"rgba(200,168,122,0.06)", borderRadius:"10px",
          padding:"14px 16px", marginBottom: generating ? "20px" : "12px" }}>
          <div style={{ fontSize:"16px", color:"#f5ede0", fontWeight:"500" }}>
            ({draft.lunches} + {draft.dinners}) × {draft.people} = {need.total} servings to cover
          </div>
          <div style={{ fontSize:"14px", color:"#a08560", marginTop:"4px" }}>
            {need.lunch} for lunches · {need.dinner} for dinners · recipes with no serving size count as 4
          </div>
        </div>

        {generating ? (
          <>
            <div role="progressbar" aria-label="Generating plan" style={{ position:"relative",
              height:"6px", borderRadius:"3px", background:"rgba(200,168,122,0.15)",
              overflow:"hidden", marginBottom:"10px" }}>
              <div style={{ position:"absolute", top:0, left:0, height:"100%", width:"40%",
                borderRadius:"3px", background:"#c8a87a",
                animation:"miseSlide 1.4s ease-in-out infinite" }} />
            </div>
            <div style={{ fontSize:"14px", marginBottom:"22px" }}>
              Choosing whole recipes that cover your servings. Usually 10 to 20 seconds.
            </div>
          </>
        ) : (
          <div style={{ display:"flex", alignItems:"center", justifyContent:"space-between",
            gap:"12px", marginBottom:"22px", minHeight:"32px" }}>
            <div style={{ fontSize:"14px", color:"#a08560" }}>
              {status || (isDefault
                ? `Using your defaults (${settingsLabel(defaults)}).`
                : `This week only. Defaults stay ${settingsLabel(defaults)}.`)}
            </div>
            <button disabled={isDefault} onClick={() => setDraft(defaults)} style={{
              height:"32px", padding:"0 12px", borderRadius:"8px", background:"transparent",
              border:"1px solid rgba(200,168,122,0.3)", color:"#c8a87a", fontSize:"14px",
              fontFamily:BUTTON_FONT, flexShrink:0,
              cursor: isDefault ? "default" : "pointer", opacity: isDefault ? 0.35 : 1,
            }}>Use defaults</button>
          </div>
        )}

        <div style={{ display:"flex", justifyContent:"flex-end", gap:"10px" }}>
          <button onClick={onClose} style={ghost}>{generating ? "Hide, keep working" : "Cancel"}</button>
          <button disabled={primaryOff} onClick={() => onGenerate(draft)} style={{
            height:"48px", padding:"0 24px", borderRadius:"10px", border:"none",
            background:"#c8a87a", color:"#120d07", fontSize:"16px", fontWeight:"600",
            fontFamily:BUTTON_FONT, cursor: primaryOff ? "default" : "pointer",
            opacity: primaryOff ? 0.45 : 1,
          }}>{generating ? "Generating…" : "Generate plan"}</button>
        </div>

        {vegFor && (
          <div style={{ fontSize:"14px", color:"#a08560", marginTop:"16px" }}>
            Currently suggesting vegetarian meals for {vegFor}. Edit this in{" "}
            <a href="?tab=settings" target="_blank" rel="noopener noreferrer"
              style={{ color:"#c8a87a" }}>Settings</a>.
          </div>
        )}
      </div>
    </div>
  );
}

// Same limits as the "Public edit recipe basics" policy in schema.sql 6f. The
// vegetarian tag counts toward the tag limit.
const MAX_SERVINGS = 25;
const MAX_TAGS = 10;

// Edits the three recipe fields the planner depends on. Vegetarian is a switch
// over the "vegetarian" tag, since the planner filters on that exact tag.
function EditRecipeModal({ recipe, onSaved, onClose }) {
  const [mealType, setMealType] = useState(recipe.meal_type);
  const [servings, setServings] = useState(Math.min(recipe.servings || 4, MAX_SERVINGS));
  const [vegetarian, setVegetarian] = useState((recipe.tags || []).includes("vegetarian"));
  const [tags, setTags] = useState((recipe.tags || []).filter(t => t !== "vegetarian"));
  const [draftTag, setDraftTag] = useState("");
  const [status, setStatus] = useState("");

  const tagCount = tags.length + (vegetarian ? 1 : 0);

  const addTag = () => {
    const t = draftTag.trim().toLowerCase();
    if (t === "vegetarian") setVegetarian(true);
    else if (t && !tags.includes(t)) setTags([...tags, t]);
    setDraftTag("");
  };

  const save = async () => {
    // Recipes ingested before the limit can already be over it.
    if (tagCount > MAX_TAGS) {
      setStatus(`A recipe can have at most ${MAX_TAGS} tags. Remove ${tagCount - MAX_TAGS}.`);
      return;
    }
    setStatus("Saving…");
    const { data, error } = await supabase.from("recipes")
      .update({ meal_type: mealType, servings, tags: vegetarian ? [...tags, "vegetarian"] : tags })
      .eq("id", recipe.id).select().maybeSingle();
    if (error || !data) {
      setStatus(`Couldn't save: ${error?.message || "not allowed (apply schema.sql 6f?)"}`);
      return;
    }
    onSaved(data);
  };

  const row = { display:"flex", alignItems:"center", justifyContent:"space-between", gap:"16px",
    padding:"12px 0", borderBottom:"1px solid rgba(200,168,122,0.15)" };
  const label = { fontSize:"17px", fontWeight:"500", color:"#f5ede0" };
  return (
    <div style={{ position:"fixed", inset:0, background:"rgba(8,5,2,0.72)", zIndex:100,
      display:"flex", alignItems:"center", justifyContent:"center", padding:"24px" }}>
      <div role="dialog" aria-modal="true" aria-labelledby="edit-title" style={{
        width:"100%", maxWidth:"480px", maxHeight:"100%", overflowY:"auto",
        background:"#1c140c", border:"1px solid rgba(200,168,122,0.3)", borderRadius:"16px",
        padding:"28px 28px 24px", boxShadow:"0 24px 60px rgba(0,0,0,0.55)" }}>
        <div style={{ fontSize:"11px", letterSpacing:"0.16em", textTransform:"uppercase",
          color:"#a08560", marginBottom:"8px" }}>Edit recipe</div>
        <h2 id="edit-title" style={{ fontFamily:"'Playfair Display',Georgia,serif",
          fontSize:"22px", fontWeight:"700", color:"#f5ede0", lineHeight:"1.2",
          margin:"0 0 12px" }}>{recipe.name}</h2>

        <div style={{ ...row, borderTop:"1px solid rgba(200,168,122,0.15)" }}>
          <div style={label}>Meal</div>
          <div style={{ display:"flex", gap:"6px" }}>
            {["lunch", "dinner"].map(m => (
              <button key={m} aria-pressed={mealType === m} onClick={() => setMealType(m)} style={{
                height:"40px", padding:"0 18px", borderRadius:"10px", fontSize:"15px",
                fontFamily:BUTTON_FONT, cursor:"pointer",
                border:"1px solid rgba(200,168,122,0.3)",
                background: mealType === m ? "#c8a87a" : "transparent",
                color: mealType === m ? "#120d07" : "#c8a87a",
                fontWeight: mealType === m ? "600" : "400" }}>{m}</button>
            ))}
          </div>
        </div>

        <div style={row}>
          <div>
            <div style={label}>Servings</div>
            {!recipe.servings && (
              <div style={{ fontSize:"14px", color:"#a08560" }}>None saved; 4 is assumed.</div>
            )}
          </div>
          <Stepper field={{ chip:"servings", min:1, max:MAX_SERVINGS }} value={servings} onChange={setServings} />
        </div>

        <div style={row}>
          <div style={label}>Vegetarian</div>
          <Toggle on={vegetarian} label="Vegetarian" onChange={setVegetarian} />
        </div>

        <div style={{ padding:"12px 0 4px" }}>
          <div style={{ ...label, marginBottom:"8px" }}>
            Other tags{" "}
            <span style={{ fontSize:"14px", fontWeight:"400", color:"#a08560" }}>
              ({tagCount} of {MAX_TAGS} used)
            </span>
          </div>
          <div style={{ display:"flex", flexWrap:"wrap", gap:"6px", marginBottom:"10px" }}>
            {tags.map(t => (
              <button key={t} aria-label={`Remove ${t}`} onClick={() => setTags(tags.filter(x => x !== t))}
                style={{ padding:"4px 10px", borderRadius:"20px", fontSize:"13px",
                  fontFamily:BUTTON_FONT, cursor:"pointer", color:"#c8a87a",
                  background:"rgba(200,168,122,0.1)", border:"1px solid rgba(200,168,122,0.25)" }}>
                {t} ×
              </button>
            ))}
            {!tags.length && <span style={{ fontSize:"14px", color:"#a08560" }}>None</span>}
          </div>
          <div style={{ display:"flex", gap:"8px" }}>
            <input value={draftTag} onChange={e => setDraftTag(e.target.value)}
              onKeyDown={e => e.key === "Enter" && addTag()} aria-label="New tag"
              placeholder="Add a tag, e.g. make-ahead" style={{
                flex:1, height:"40px", padding:"0 12px", borderRadius:"8px", outline:"none",
                background:"rgba(255,252,245,0.04)", border:"1px solid rgba(200,168,122,0.25)",
                fontFamily:BUTTON_FONT, fontSize:"15px", color:"#f5ede0" }} />
            <button onClick={addTag} style={{ height:"40px", padding:"0 16px", borderRadius:"8px",
              fontFamily:BUTTON_FONT, fontSize:"15px", cursor:"pointer", color:"#c8a87a",
              background:"transparent", border:"1px solid rgba(200,168,122,0.3)" }}>Add</button>
          </div>
        </div>

        {status && <div style={{ fontSize:"14px", color:"#a08560", marginTop:"12px" }}>{status}</div>}

        <div style={{ display:"flex", justifyContent:"flex-end", gap:"10px", marginTop:"22px" }}>
          <button onClick={onClose} style={{ height:"48px", padding:"0 20px", borderRadius:"10px",
            background:"transparent", border:"1px solid rgba(200,168,122,0.3)", color:"#c8a87a",
            fontSize:"15px", cursor:"pointer", fontFamily:BUTTON_FONT }}>Cancel</button>
          <button onClick={save} style={{ height:"48px", padding:"0 24px", borderRadius:"10px",
            border:"none", background:"#c8a87a", color:"#120d07", fontSize:"16px",
            fontWeight:"600", cursor:"pointer", fontFamily:BUTTON_FONT }}>Save</button>
        </div>
      </div>
    </div>
  );
}

// ─── Main App ───────────────────────────────────────────────────────────────

export default function MiseEnPlace() {
  // ?tab=settings opens that tab, so other pages can link straight to it.
  const [tab, setTab] = useState(() => {
    const t = new URLSearchParams(window.location.search).get("tab");
    return ["week", "shopping", "library", "queue", "settings"].includes(t) ? t : "week";
  });
  const [plan, setPlan] = useState(null);          // this week's weekly_plans row, or null
  const [allRecipes, setAllRecipes] = useState([]);
  const [shoppingItems, setShoppingItems] = useState([]);
  const [loading, setLoading] = useState(true);
  const [swapTarget, setSwapTarget] = useState(null);
  const [saved, setSaved] = useState(false);
  const [queueInput, setQueueInput] = useState("");
  const [queueFeedback, setQueueFeedback] = useState("");
  const [triggering, setTriggering] = useState(false);
  const [triggerStatus, setTriggerStatus] = useState("");
  const [defaults, setDefaults] = useState(DEFAULT_SETTINGS);   // meal_settings row
  const [modalOpen, setModalOpen] = useState(false);
  const [editTarget, setEditTarget] = useState(null);   // recipe open in the edit dialog
  const triggerPollRef = useRef(null);
  const weekStart = planMonday();

  // Resolve the plan's lunch/dinner IDs to full recipes, preserving array order
  // (json_agg in the SQL view doesn't guarantee order, so we resolve client-side).
  const recipesById = useMemo(
    () => Object.fromEntries(allRecipes.map(r => [r.id, r])), [allRecipes]
  );
  const lunches = (plan?.lunches || []).map(id => recipesById[id]).filter(Boolean);
  const dinners = (plan?.dinners || []).map(id => recipesById[id]).filter(Boolean);

  useEffect(() => {
    (async () => {
      const [{ data: recipes }, { data: p }, { data: s }] = await Promise.all([
        supabase.from("recipes").select("*"),
        supabase.from("weekly_plans").select("*").eq("week_start", weekStart).maybeSingle(),
        // Missing until schema.sql 6d is applied; the defaults stand in.
        supabase.from("meal_settings").select("*").eq("id", 1).maybeSingle(),
      ]);
      setAllRecipes(recipes || []);
      if (s) setDefaults(withDefaults(s));
      setPlan(p);
      setShoppingItems(p?.shopping_list || []);
      setLoading(false);
    })();
  }, []);

  useEffect(() => () => { if (triggerPollRef.current) clearInterval(triggerPollRef.current); }, []);

  const openModal = () => { setTriggerStatus(""); setModalOpen(true); };

  // `settings` is this week's lunches/dinners/people from the Generate dialog.
  const handleTrigger = async (settings) => {
    setTriggering(true);
    setTriggerStatus("Generating plan…");

    const { data: before } = await supabase.from("weekly_plans")
      .select("updated_at").eq("week_start", weekStart).maybeSingle();
    const startUpdated = before?.updated_at ?? null;

    try {
      const resp = await fetch(`${import.meta.env.VITE_INGEST_URL}/trigger/saturday-suggest`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-Ingest-Token": import.meta.env.VITE_INGEST_TOKEN,
        },
        body: JSON.stringify(settings),
      });
      if (!resp.ok) {
        const data = await resp.json().catch(() => ({}));
        setTriggerStatus(`Error: ${data.detail || "Failed to trigger"}`);
        setTriggering(false);
        return;
      }
    } catch {
      setTriggerStatus("Network error — is the ingest service running?");
      setTriggering(false);
      return;
    }

    // Poll until the row changes. Regenerating upserts the same row (same id),
    // so compare updated_at, read from the DB: swaps, confirms and Clear bump it
    // without touching the copy in state.
    let attempts = 0;
    triggerPollRef.current = setInterval(async () => {
      attempts++;
      if (attempts > 40) {
        clearInterval(triggerPollRef.current);
        setTriggerStatus("Taking longer than expected — check back in a few minutes.");
        setTriggering(false);
        return;
      }
      const { data: latest } = await supabase
        .from("weekly_plans")
        .select("*")
        .eq("week_start", weekStart)
        .maybeSingle();
      if (latest && latest.updated_at !== startUpdated) {
        clearInterval(triggerPollRef.current);
        setPlan(latest);
        setShoppingItems(latest.shopping_list || []);
        setTriggering(false);
        setTriggerStatus("");
        setModalOpen(false);
      }
    }, 3000);
  };

  const handleSwap = async (original, replacement) => {
    if (!plan) return;
    // Which list the recipe is in, not its meal_type: editing a recipe's meal
    // type must not make a swap write to the wrong list.
    const field = plan.lunches?.includes(original.id) ? "lunches" : "dinners";
    const nextIds = (plan[field] || []).map(id => id === original.id ? replacement.id : id);
    setPlan(p => ({ ...p, [field]: nextIds }));   // optimistic
    setSwapTarget(null);
    await supabase.from("weekly_plans").update({ [field]: nextIds }).eq("id", plan.id);
  };

  const handleSave = async () => {
    if (!plan) return;
    setSaved(true);
    setTimeout(() => setSaved(false), 2500);
    await supabase.from("weekly_plans")
      .update({ status: "confirmed", confirmed_at: new Date().toISOString() })
      .eq("id", plan.id);
  };

  // Empties the row rather than deleting it: the anon key can UPDATE the current
  // week's plan but not DELETE, and Generate upserts the same row back to life.
  const handleClear = async () => {
    if (!plan || !window.confirm("Clear this week's plan and shopping list?")) return;
    const cleared = { lunches: [], dinners: [], shopping_list: [], status: "suggested", confirmed_at: null, notes: null };
    setPlan(p => ({ ...p, ...cleared }));
    setShoppingItems([]);
    await supabase.from("weekly_plans").update(cleared).eq("id", plan.id);
  };

  const loadRecipes = async () => {
    const { data } = await supabase.from("recipes").select("*");
    if (data) setAllRecipes(data);
  };

  // Puts a library recipe on the queue, or takes it off. Either way the recipe
  // stays in the library.
  const setQueuedState = async (r, on) => {
    const apply = v =>
      setAllRecipes(rs => rs.map(x => x.id === r.id ? { ...x, in_queue: v } : x));
    apply(on);   // optimistic
    const { error } = await supabase.from("recipes").update({ in_queue: on }).eq("id", r.id);
    if (error) {
      apply(!on);
      window.alert(`Couldn't ${on ? "queue" : "remove"} "${r.name}": ${error.message}`);
    }
  };

  const handleQueueSubmit = async () => {
    const input = queueInput.trim();
    if (!input) return;

    if (!/^https?:\/\//i.test(input)) {
      setQueueFeedback("⚠️ Paste a recipe link (starting with http).");
      setTimeout(() => setQueueFeedback(""), 4000);
      return;
    }

    setQueueFeedback("⏳ Adding…");
    try {
      const resp = await fetch(`${import.meta.env.VITE_INGEST_URL}/ingest`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-Ingest-Token": import.meta.env.VITE_INGEST_TOKEN,
        },
        body: JSON.stringify({ url: input }),
      });
      const data = await resp.json().catch(() => ({}));
      if (resp.ok) {
        setQueueFeedback(`${data.duplicate ? "ℹ️" : "✅"} ${data.message || "Added to queue!"}`);
        setQueueInput("");
        loadRecipes();   // the queue list comes from state, not from the response
      } else {
        setQueueFeedback(`⚠️ ${data.detail || "Couldn't add that. Try again."}`);
      }
    } catch (err) {
      setQueueFeedback("⚠️ Network error — is the ingest service running?");
    }
    setTimeout(() => setQueueFeedback(""), 4000);
  };

  const queued = allRecipes.filter(r => r.in_queue);
  const hasPlan = !!(plan?.lunches?.length || plan?.dinners?.length);
  // The settings the plan was generated with (null for plans from before this
  // existed, or if schema.sql 6d isn't applied), live against the current picks.
  const planSettings = hasPlan ? plan.settings : null;
  const planned = totalServings([...lunches, ...dinners]);
  const need = planSettings && servingsNeeded(planSettings).total;
  const planSummary = !planSettings ? `${planned} servings planned`
    : planned >= need
      ? `${need} servings needed · ${planned} planned · ${planned - need} leftover`
      : `${need} servings needed · ${planned} planned · ${need - planned} short`;
  const weekOf = formatWeek(weekStart, { month: "long", day: "numeric", year: "numeric" });
  const weekLabel = `Week of ${formatWeek(weekStart, { month: "short", day: "numeric" })}`;

  return (
    <>
      <style>{`
        @import url('https://fonts.googleapis.com/css2?family=Playfair+Display:ital,wght@0,400;0,600;0,700;1,400&family=Crimson+Pro:wght@300;400;500&display=swap');
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body { background: #120d07; }
        ::-webkit-scrollbar { width: 4px; }
        ::-webkit-scrollbar-track { background: transparent; }
        ::-webkit-scrollbar-thumb { background: rgba(200,168,122,0.2); border-radius: 2px; }
        @keyframes miseSlide { 0% { transform: translateX(-100%); } 100% { transform: translateX(250%); } }
      `}</style>

      <div style={{
        minHeight:"100vh", background:"#120d07",
        color:"#c8a87a", fontFamily:"'Crimson Pro', Georgia, serif",
      }}>
        {/* Grain overlay */}
        <div style={{
          position:"fixed", inset:0, pointerEvents:"none", zIndex:0,
          backgroundImage:`url("data:image/svg+xml,%3Csvg viewBox='0 0 256 256' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='noise'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='4' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23noise)' opacity='0.04'/%3E%3C/svg%3E")`,
          opacity:0.5
        }} />

        <div style={{ position:"relative", zIndex:1, maxWidth:"720px",
          margin:"0 auto", padding:"0 16px" }}>

          {/* Header */}
          <div style={{ padding:"32px 0 24px", borderBottom:"1px solid rgba(200,168,122,0.1)" }}>
            <div style={{ display:"flex", justifyContent:"space-between", alignItems:"flex-end" }}>
              <div>
                <div style={{ fontSize:"11px", letterSpacing:"0.2em",
                  color:"#806040", marginBottom:"6px", textTransform:"uppercase" }}>
                  Mise en Place
                </div>
                <h1 style={{ fontFamily:"'Playfair Display',Georgia,serif",
                  fontSize:"32px", fontWeight:"700", color:"#f5ede0",
                  lineHeight:"1.1" }}>
                  {loading ? "Loading…" : `Week of ${weekOf}`}
                </h1>
              </div>
              <div style={{ textAlign:"right" }}>
                <div style={{ fontSize:"11px", color:"#5a4030" }}>Next shop</div>
                <div style={{ fontSize:"14px", color:"#c8a87a", fontWeight:"500" }}>Sunday</div>
              </div>
            </div>
          </div>

          {/* Nav */}
          <div style={{ display:"flex", gap:"0", borderBottom:"1px solid rgba(200,168,122,0.1)",
            marginBottom:"28px" }}>
            {[
              { id:"week",     label:"This Week" },
              { id:"shopping", label:"Shopping" },
              { id:"library",  label:"Library"  },
              { id:"queue",    label:`Queue (${queued.length})` },
              { id:"settings", label:"Settings" },
            ].map(t => (
              <button key={t.id} onClick={() => setTab(t.id)} style={{
                padding:"14px 20px", background:"transparent", border:"none",
                fontSize:"14px", cursor:"pointer", position:"relative",
                color: tab === t.id ? "#f5ede0" : "#5a4030",
                fontFamily:"'Crimson Pro',Georgia,serif",
                fontWeight: tab === t.id ? "500" : "400",
                borderBottom: tab === t.id
                  ? "2px solid #c8a87a" : "2px solid transparent",
                transition:"all 0.2s",
              }}>{t.label}</button>
            ))}
          </div>

          {/* ── THIS WEEK TAB ── */}
          {tab === "week" && (
            loading ? (
              <EmptyState>Loading this week's plan…</EmptyState>
            ) : !hasPlan ? (
              <div style={{ textAlign:"center", padding:"60px 20px" }}>
                <div style={{ color:"#5a4030", fontSize:"14px",
                  lineHeight:"1.6", marginBottom:"24px" }}>
                  No plan yet. Saturday's cron picks recipes to cover your weekly
                  servings — or generate one now.
                </div>
                <button
                  onClick={openModal}
                  disabled={triggering}
                  style={{
                    padding:"12px 28px",
                    background: triggering ? "rgba(200,168,122,0.05)" : "rgba(200,168,122,0.12)",
                    border:"1px solid rgba(200,168,122,0.3)", borderRadius:"10px",
                    color: triggering ? "#5a4030" : "#c8a87a",
                    fontSize:"14px", fontWeight:"600", cursor: triggering ? "default" : "pointer",
                    fontFamily:"'Crimson Pro',Georgia,serif", transition:"all 0.2s",
                  }}
                >
                  {triggering ? "⏳ Generating…" : "Generate Plan Now"}
                </button>
                {triggerStatus && (
                  <div style={{ fontSize:"12px", color:"#806040", marginTop:"12px" }}>
                    {triggerStatus}
                  </div>
                )}
              </div>
            ) : (
            <div>
              <div style={{ fontSize:"14px", color:"#a08560", marginBottom:"20px" }}>
                {planSummary}
              </div>
              <div style={{ marginBottom:"28px" }}>
                <div style={{ display:"flex", justifyContent:"space-between",
                  alignItems:"center", marginBottom:"14px" }}>
                  <h2 style={{ fontFamily:"'Playfair Display',Georgia,serif",
                    fontSize:"20px", color:"#f5ede0" }}>
                    Meal-Prep Lunches
                  </h2>
                  <span style={{ fontSize:"11px", color:"#5a4030" }}>
                    {totalServings(lunches)} servings · prep Sunday · eat Mon–Fri
                  </span>
                </div>
                <div style={{ display:"flex", flexDirection:"column", gap:"10px" }}>
                  {lunches.map(r => (
                    <RecipeCard key={r.id} recipe={r} onSwap={() => setSwapTarget(r)} />
                  ))}
                </div>
              </div>

              <div style={{ marginBottom:"28px" }}>
                <div style={{ display:"flex", justifyContent:"space-between",
                  alignItems:"center", marginBottom:"14px" }}>
                  <h2 style={{ fontFamily:"'Playfair Display',Georgia,serif",
                    fontSize:"20px", color:"#f5ede0" }}>
                    Dinners
                  </h2>
                  <span style={{ fontSize:"11px", color:"#5a4030" }}>
                    {totalServings(dinners)} servings · cook fresh each night
                  </span>
                </div>
                <div style={{ display:"flex", flexDirection:"column", gap:"10px" }}>
                  {dinners.map(r => (
                    <RecipeCard key={r.id} recipe={r} onSwap={() => setSwapTarget(r)} />
                  ))}
                </div>
              </div>

              <div style={{ display:"flex", gap:"10px", paddingBottom:"32px" }}>
                <button onClick={handleSave} style={{
                  flex:1, padding:"14px",
                  background: saved ? "rgba(120,200,120,0.1)" : "rgba(200,168,122,0.08)",
                  border: `1px solid ${saved ? "rgba(120,200,120,0.3)" : "rgba(200,168,122,0.2)"}`,
                  borderRadius:"10px", color: saved ? "#80c880" : "#c8a87a",
                  fontSize:"14px", fontWeight:"600", cursor:"pointer",
                  fontFamily:"'Crimson Pro',Georgia,serif",
                  transition:"all 0.3s"
                }}>
                  {saved ? "✓ Plan Confirmed" : "Confirm This Week's Plan"}
                </button>
                <button
                  onClick={openModal}
                  disabled={triggering}
                  title="Regenerate plan"
                  style={{
                    padding:"14px 18px",
                    background: triggering ? "rgba(200,168,122,0.03)" : "rgba(200,168,122,0.06)",
                    border:"1px solid rgba(200,168,122,0.15)", borderRadius:"10px",
                    color: triggering ? "#5a4030" : "#806040",
                    fontSize:"16px", cursor: triggering ? "default" : "pointer",
                    transition:"all 0.2s",
                  }}
                >
                  {triggering ? "⏳" : "↺"}
                </button>
                <button
                  onClick={handleClear}
                  disabled={triggering}
                  title="Clear plan"
                  style={{
                    padding:"14px 18px", background:"rgba(200,168,122,0.06)",
                    border:"1px solid rgba(200,168,122,0.15)", borderRadius:"10px",
                    color:"#806040", fontSize:"16px", cursor:"pointer",
                  }}
                >
                  🗑
                </button>
              </div>
              {triggerStatus && (
                <div style={{ fontSize:"12px", color:"#806040",
                  marginTop:"-16px", marginBottom:"16px", textAlign:"center" }}>
                  {triggerStatus}
                </div>
              )}
            </div>
            )
          )}

          {/* ── SHOPPING TAB ── */}
          {tab === "shopping" && (
            loading ? (
              <EmptyState>Loading…</EmptyState>
            ) : !plan ? (
              <EmptyState>No plan yet — the shopping list appears once a plan is set.</EmptyState>
            ) : (
              <ShoppingTab
                items={shoppingItems}
                setItems={setShoppingItems}
                planId={plan.id}
                weekLabel={weekLabel}
              />
            )
          )}

          {/* ── LIBRARY TAB ── */}
          {tab === "library" && (
            loading ? (
              <EmptyState>Loading recipes…</EmptyState>
            ) : (
              <LibraryTab recipes={allRecipes} onEdit={setEditTarget}
                onQueue={r => setQueuedState(r, true)} />
            )
          )}

          {/* ── SETTINGS TAB ── */}
          {tab === "settings" && (
            loading ? (
              <EmptyState>Loading…</EmptyState>
            ) : (
              <SettingsTab defaults={defaults} onSaved={setDefaults} />
            )
          )}

          {/* ── QUEUE TAB ── */}
          {tab === "queue" && (
            <div>
              <h2 style={{ fontFamily:"'Playfair Display',Georgia,serif",
                fontSize:"22px", color:"#f5ede0", marginBottom:"6px" }}>
                Recipe Queue
              </h2>
              <p style={{ fontSize:"13px", color:"#806040", marginBottom:"20px" }}>
                These recipes are prioritised for upcoming weeks. Add one below by
                pasting a recipe link. Anything already in your library or queue
                won't be added twice. To queue a recipe that's already in the
                library, use <b style={{ fontWeight:"500" }}>add to queue</b> on its
                card in the Library tab.
              </p>

              {/* Add manually */}
              <div style={{ marginBottom:"24px", padding:"18px",
                background:"rgba(255,252,245,0.03)",
                border:"1px solid rgba(200,168,122,0.12)", borderRadius:"12px" }}>
                <div style={{ fontSize:"12px", color:"#806040",
                  letterSpacing:"0.08em", marginBottom:"10px" }}>
                  ADD TO QUEUE
                </div>
                <div style={{ display:"flex", gap:"8px" }}>
                  <input
                    value={queueInput}
                    onChange={e => setQueueInput(e.target.value)}
                    onKeyDown={e => e.key === "Enter" && handleQueueSubmit()}
                    placeholder="Paste a recipe link…"
                    style={{
                      flex:1, background:"rgba(255,252,245,0.04)",
                      border:"1px solid rgba(200,168,122,0.15)",
                      borderRadius:"8px", padding:"10px 14px",
                      fontSize:"13px", color:"#f5ede0", outline:"none",
                      fontFamily:"'Crimson Pro',Georgia,serif"
                    }}
                  />
                  <button onClick={handleQueueSubmit} style={{
                    padding:"10px 18px", background:"rgba(200,168,122,0.1)",
                    border:"1px solid rgba(200,168,122,0.25)", borderRadius:"8px",
                    color:"#c8a87a", cursor:"pointer", fontSize:"13px",
                    fontFamily:"'Crimson Pro',Georgia,serif"
                  }}>Add</button>
                </div>
                {queueFeedback && (
                  <div style={{ fontSize:"12px", color:"#80c880", marginTop:"8px" }}>
                    {queueFeedback}
                  </div>
                )}
              </div>

              {loading ? (
                <div style={{ textAlign:"center", padding:"40px",
                  color:"#5a4030", fontSize:"14px" }}>
                  Loading…
                </div>
              ) : queued.length === 0 ? (
                <div style={{ textAlign:"center", padding:"40px",
                  color:"#5a4030", fontSize:"14px" }}>
                  Queue is empty — paste a recipe link above to add one!
                </div>
              ) : (
                <div style={{ display:"flex", flexDirection:"column", gap:"10px" }}>
                  {queued.map((r, i) => (
                    <div key={r.id} style={{ display:"flex",
                      alignItems:"stretch", gap:"0" }}>
                      <div style={{ width:"32px", display:"flex",
                        alignItems:"center", justifyContent:"center",
                        fontSize:"13px", color:"#5a4030",
                        fontFamily:"'Playfair Display',serif",
                        fontStyle:"italic" }}>
                        {i+1}
                      </div>
                      <div style={{ flex:1 }}>
                        <RecipeCard recipe={r} onEdit={setEditTarget}
                          onRemove={r => setQueuedState(r, false)} />
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>
      </div>

      {/* Generate dialog: remounts on open, so it starts from this week's values */}
      {modalOpen && (
        <GenerateModal
          initial={planSettings || defaults}
          defaults={defaults}
          weekLabel={weekLabel}
          generating={triggering}
          status={triggerStatus}
          onGenerate={handleTrigger}
          onClose={() => setModalOpen(false)}
        />
      )}

      {editTarget && (
        <EditRecipeModal
          recipe={editTarget}
          onSaved={row => {
            setAllRecipes(rs => rs.map(r => r.id === row.id ? row : r));
            setEditTarget(null);
          }}
          onClose={() => setEditTarget(null)}
        />
      )}

      {/* Swap modal */}
      {swapTarget && (
        <SwapModal
          recipe={swapTarget}
          allRecipes={allRecipes}
          onConfirm={handleSwap}
          onClose={() => setSwapTarget(null)}
        />
      )}
    </>
  );
}
