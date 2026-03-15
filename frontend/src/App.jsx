import { useState, useEffect } from "react";

// ─── Mock data (replace with Supabase client calls) ───────────────────────

const MOCK_LUNCHES = [
  { id: "1", name: "Greek Chicken Meal Prep Bowls", description: "Lemony marinated chicken thighs over rice with cucumber, tomato, and tzatziki.", tags: ["make-ahead","high-protein"], prep_time_mins: 20, cook_time_mins: 30, servings: 5, meal_type: "lunch", source_url: null },
  { id: "2", name: "Spicy Sesame Noodles", description: "Cold sesame noodles with chili crisp, edamame, and shredded rotisserie chicken.", tags: ["cold","quick","asian"], prep_time_mins: 15, cook_time_mins: 10, servings: 5, meal_type: "lunch", source_url: null },
  { id: "3", name: "White Bean & Kale Soup", description: "Hearty Tuscan-style soup that thickens beautifully overnight.", tags: ["vegetarian","healthy"], prep_time_mins: 15, cook_time_mins: 35, servings: 6, meal_type: "lunch", source_url: null },
  { id: "4", name: "Chipotle-Style Burrito Bowls", description: "Rice, black beans, grilled chicken, corn salsa, and sour cream.", tags: ["make-ahead","high-protein"], prep_time_mins: 20, cook_time_mins: 25, servings: 5, meal_type: "lunch", source_url: null },
  { id: "5", name: "Roasted Veggie & Farro Salad", description: "Warm or cold farro with roasted beets, goat cheese, and lemon tahini.", tags: ["vegetarian","make-ahead"], prep_time_mins: 15, cook_time_mins: 40, servings: 5, meal_type: "lunch", source_url: null },
];

const MOCK_DINNERS = [
  { id: "6", name: "Smash Burgers", description: "Crispy-edged beef patties with American cheese, pickles, and special sauce.", tags: ["quick","crowd-pleaser"], prep_time_mins: 10, cook_time_mins: 15, servings: 4, meal_type: "dinner", source_url: null },
  { id: "7", name: "Sheet Pan Salmon", description: "Salmon fillets with asparagus and cherry tomatoes, lemon-dill butter.", tags: ["healthy","one-pan"], prep_time_mins: 10, cook_time_mins: 25, servings: 4, meal_type: "dinner", source_url: null },
  { id: "8", name: "Birria Tacos", description: "Slow-braised beef birria with consommé for dipping. Weekend showstopper.", tags: ["weekend","slow-cook"], prep_time_mins: 30, cook_time_mins: 240, servings: 6, meal_type: "dinner", source_url: null },
];

const MOCK_ALL_RECIPES = [
  ...MOCK_LUNCHES, ...MOCK_DINNERS,
  { id: "9", name: "One-Pot Pasta e Fagioli", description: "Italian bean and pasta soup.", tags: ["vegetarian","quick"], prep_time_mins: 10, cook_time_mins: 25, servings: 4, meal_type: "dinner", source_url: null },
  { id: "10", name: "Korean BBQ Chicken Rice Bowls", description: "Gochujang-marinated chicken over steamed rice with pickled veggies.", tags: ["make-ahead","asian"], prep_time_mins: 20, cook_time_mins: 20, servings: 5, meal_type: "lunch", source_url: null, in_queue: true },
  { id: "11", name: "Creamy Tuscan White Bean Pasta", description: "Silky pasta with white beans, sun-dried tomatoes, and spinach.", tags: ["vegetarian","quick"], prep_time_mins: 10, cook_time_mins: 20, servings: 4, meal_type: "dinner", source_url: "https://instagram.com/p/example", in_queue: true },
];

const SHOPPING_LIST = [
  { name: "chicken thighs", amount: "3 lbs", category: "protein", checked: false, for_recipes: ["Greek Chicken Bowls"] },
  { name: "salmon fillets", amount: "4 × 6oz", category: "protein", checked: false, for_recipes: ["Sheet Pan Salmon"] },
  { name: "80/20 ground beef", amount: "1.5 lbs", category: "protein", checked: true, for_recipes: ["Smash Burgers"] },
  { name: "jasmine rice", amount: "2 cups", category: "grain", checked: false, for_recipes: ["Greek Chicken Bowls"] },
  { name: "soba noodles", amount: "12 oz", category: "grain", checked: false, for_recipes: ["Sesame Noodles"] },
  { name: "cherry tomatoes", amount: "2 pints", category: "produce", checked: false, for_recipes: ["Greek Chicken Bowls","Sheet Pan Salmon"] },
  { name: "cucumber", amount: "1 large", category: "produce", checked: true, for_recipes: ["Greek Chicken Bowls"] },
  { name: "asparagus", amount: "1 bunch", category: "produce", checked: false, for_recipes: ["Sheet Pan Salmon"] },
  { name: "kale", amount: "1 bunch", category: "produce", checked: false, for_recipes: ["White Bean Soup"] },
  { name: "lemon", amount: "5", category: "produce", checked: false, for_recipes: ["Greek Chicken Bowls","Sheet Pan Salmon"] },
  { name: "tzatziki", amount: "1 cup", category: "dairy", checked: false, for_recipes: ["Greek Chicken Bowls"] },
  { name: "butter", amount: "4 tbsp", category: "dairy", checked: true, for_recipes: ["Sheet Pan Salmon"] },
  { name: "brioche buns", amount: "4", category: "bakery", checked: false, for_recipes: ["Smash Burgers"] },
  { name: "chili crisp", amount: "1 jar", category: "pantry", checked: false, for_recipes: ["Sesame Noodles"] },
  { name: "sesame oil", amount: "1 bottle", category: "pantry", checked: true, for_recipes: ["Sesame Noodles"] },
  { name: "cannellini beans", amount: "2 cans", category: "pantry", checked: false, for_recipes: ["White Bean Soup"] },
];

const CATEGORY_ORDER = ["produce","protein","dairy","grain","bakery","pantry","condiment","frozen","other"];
const CATEGORY_EMOJI = { produce:"🥬", protein:"🥩", dairy:"🧀", grain:"🌾", bakery:"🍞", pantry:"🫙", condiment:"🧴", frozen:"🧊", other:"📦" };

// ─── Components ────────────────────────────────────────────────────────────

function RecipeCard({ recipe, onSwap, compact = false }) {
  const totalMins = (recipe.prep_time_mins || 0) + (recipe.cook_time_mins || 0);
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
          fontSize:"10px", color:"#c8a87a" }}>📸 IG</div>
      )}
	{recipe.vegetarian && (
          <div style={{ position:"absolute", top: recipe.in_queue ? 52 : 30, right:10,
            fontSize:"10px", color:"#7ac87a",
            background:"rgba(122,200,122,0.1)",
            border:"1px solid rgba(122,200,122,0.2)",
            padding:"2px 6px", borderRadius:"20px" }}>🌿 veg</div>
        )}
      <div style={{ fontFamily:"'Playfair Display', Georgia, serif",
        fontSize: compact ? "14px" : "16px", fontWeight:"700",
        color:"#f5ede0", marginBottom:"5px", paddingRight:"40px" }}>
        {recipe.name}
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
        {(recipe.tags || []).slice(0,2).map(t => (
          <span key={t} style={{ fontSize:"10px", color:"#c8a87a",
            background:"rgba(200,168,122,0.1)", padding:"2px 8px",
            borderRadius:"20px", border:"1px solid rgba(200,168,122,0.15)" }}>
            {t}
          </span>
        ))}
        {onSwap && (
          <button onClick={() => onSwap(recipe)} style={{
            marginLeft:"auto", fontSize:"11px",
            background:"rgba(200,168,122,0.1)", color:"#c8a87a",
            border:"1px solid rgba(200,168,122,0.2)", padding:"4px 12px",
            borderRadius:"6px", cursor:"pointer"
          }}>swap ↕</button>
        )}
      </div>
    </div>
  );
}

function ShoppingTab({ items, setItems }) {
  const byCategory = {};
  for (const item of items) {
    const cat = item.category || "other";
    if (!byCategory[cat]) byCategory[cat] = [];
    byCategory[cat].push(item);
  }
  const total = items.length;
  const done  = items.filter(i => i.checked).length;

  const toggle = (name) => {
    setItems(prev => prev.map(i => i.name === name ? {...i, checked: !i.checked} : i));
  };

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
            {done}/{total} items checked · Week of Mar 17
          </div>
        </div>
        <div style={{
          width:"48px", height:"48px", borderRadius:"50%",
          background:`conic-gradient(#c8a87a ${done/total*360}deg, rgba(200,168,122,0.1) 0deg)`,
          display:"flex", alignItems:"center", justifyContent:"center",
          fontSize:"12px", color:"#c8a87a", fontWeight:"700"
        }}>{Math.round(done/total*100)}%</div>
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
                      {item.amount}
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

// ─── Main App ───────────────────────────────────────────────────────────────

export default function MiseEnPlace() {
  const [tab, setTab] = useState("week");
  const [lunches, setLunches] = useState(MOCK_LUNCHES);
  const [dinners, setDinners] = useState(MOCK_DINNERS);
  const [allRecipes] = useState(MOCK_ALL_RECIPES);
  const [shoppingItems, setShoppingItems] = useState(SHOPPING_LIST);
  const [swapTarget, setSwapTarget] = useState(null);
  const [saved, setSaved] = useState(false);
  const [queueInput, setQueueInput] = useState("");
  const [queueFeedback, setQueueFeedback] = useState("");

  const handleSwap = (original, replacement) => {
    if (original.meal_type === "lunch") {
      setLunches(prev => prev.map(r => r.id === original.id ? replacement : r));
    } else {
      setDinners(prev => prev.map(r => r.id === original.id ? replacement : r));
    }
    setSwapTarget(null);
  };

  const handleSave = () => {
    setSaved(true);
    setTimeout(() => setSaved(false), 2500);
  };

  const handleQueueSubmit = () => {
    if (!queueInput.trim()) return;
    setQueueFeedback(`✅ "${queueInput.slice(0, 40)}" added to queue!`);
    setQueueInput("");
    setTimeout(() => setQueueFeedback(""), 3000);
  };

  const queued = allRecipes.filter(r => r.in_queue);
  const weekOf = "March 17, 2026";

  return (
    <>
      <style>{`
        @import url('https://fonts.googleapis.com/css2?family=Playfair+Display:ital,wght@0,400;0,600;0,700;1,400&family=Crimson+Pro:wght@300;400;500&display=swap');
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body { background: #120d07; }
        ::-webkit-scrollbar { width: 4px; }
        ::-webkit-scrollbar-track { background: transparent; }
        ::-webkit-scrollbar-thumb { background: rgba(200,168,122,0.2); border-radius: 2px; }
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
                  Week of {weekOf}
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
            <div>
              <div style={{ marginBottom:"28px" }}>
                <div style={{ display:"flex", justifyContent:"space-between",
                  alignItems:"center", marginBottom:"14px" }}>
                  <h2 style={{ fontFamily:"'Playfair Display',Georgia,serif",
                    fontSize:"20px", color:"#f5ede0" }}>
                    5 Meal-Prep Lunches
                  </h2>
                  <span style={{ fontSize:"11px", color:"#5a4030" }}>
                    prep Sunday · eat Mon–Fri
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
                    3 Dinners
                  </h2>
                  <span style={{ fontSize:"11px", color:"#5a4030" }}>
                    cook fresh each night
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
              </div>
            </div>
          )}

          {/* ── SHOPPING TAB ── */}
          {tab === "shopping" && (
            <ShoppingTab items={shoppingItems} setItems={setShoppingItems} />
          )}

          {/* ── LIBRARY TAB ── */}
          {tab === "library" && (
            <div>
              <h2 style={{ fontFamily:"'Playfair Display',Georgia,serif",
                fontSize:"22px", color:"#f5ede0", marginBottom:"6px" }}>
                Recipe Library
              </h2>
              <p style={{ fontSize:"13px", color:"#806040", marginBottom:"20px" }}>
                All {allRecipes.length} recipes. Send an Instagram link or recipe name 
                by text to add new ones.
              </p>

              {["lunch","dinner"].map(type => (
                <div key={type} style={{ marginBottom:"28px" }}>
                  <div style={{ fontSize:"11px", color:"#806040",
                    letterSpacing:"0.12em", textTransform:"uppercase",
                    marginBottom:"12px" }}>
                    {type === "lunch" ? "🥗 Lunches" : "🍽️ Dinners"}
                  </div>
                  <div style={{ display:"flex", flexDirection:"column", gap:"8px" }}>
                    {allRecipes.filter(r => r.meal_type === type).map(r => (
                      <RecipeCard key={r.id} recipe={r} compact />
                    ))}
                  </div>
                </div>
              ))}
            </div>
          )}

          {/* ── QUEUE TAB ── */}
          {tab === "queue" && (
            <div>
              <h2 style={{ fontFamily:"'Playfair Display',Georgia,serif",
                fontSize:"22px", color:"#f5ede0", marginBottom:"6px" }}>
                Recipe Queue
              </h2>
              <p style={{ fontSize:"13px", color:"#806040", marginBottom:"20px" }}>
                These recipes are prioritised for upcoming weeks. Add by texting an 
                Instagram link or recipe name to the group number.
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
                    placeholder="Paste Instagram link or recipe name…"
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

              {queued.length === 0 ? (
                <div style={{ textAlign:"center", padding:"40px",
                  color:"#5a4030", fontSize:"14px" }}>
                  Queue is empty — text a link or recipe name to add one!
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
                        <RecipeCard recipe={r} />
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>
      </div>

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
