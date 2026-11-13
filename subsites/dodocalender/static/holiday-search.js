const search = document.getElementById("country-search");
const list = document.getElementById("country-list");
const status = document.getElementById("country-results");
const estonianNames = new Intl.DisplayNames(["et"], { type: "region", fallback: "none" });

function normalize(value) {
  return value.normalize("NFD").replace(/\p{M}/gu, "").toLowerCase();
}

const countries = Array.from(list.querySelectorAll("[data-code]"), (label) => {
  const code = label.dataset.code;
  const localized = code === "GB-ENG" ? "Inglismaa" : estonianNames.of(code);
  return { label, terms: normalize(`${label.textContent} ${localized ?? ""}`) };
});

function filterCountries() {
  const terms = normalize(search.value).trim().split(/\s+/);
  let visible = 0;
  for (const country of countries) {
    const matches = terms.every((term) => country.terms.includes(term));
    country.label.hidden = !matches;
    visible += Number(matches);
  }
  list.hidden = visible === 0;
  status.textContent = visible ? `${visible} matching calendars` : "No matching countries or territories.";
}

search.addEventListener("input", filterCountries);
document.getElementById("country-search-label").hidden = false;
status.hidden = false;
filterCountries();
