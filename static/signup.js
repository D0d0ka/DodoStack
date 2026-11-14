const firstName = document.querySelector("#first-name");
const lastName = document.querySelector("#last-name");
const addressField = document.querySelector("#dmail-local");
const names = [firstName, lastName];
let automatic = addressField.readOnly || !addressField.value;
let revision = 0;

function localPart(name) {
    return name.normalize("NFKD").replace(/[^\x00-\x7f]/g, "").toLowerCase()
        .replace(/[^a-z0-9]+/g, ".").replace(/^\.|\.$/g, "").slice(0, 24)
        .replace(/\.$/, "") || "user";
}

async function suggest() {
    const [first, last] = names.map(field => field.value.trim());
    if (!first || !last) return;
    addressField.readOnly = false;
    if (!automatic) return;

    const current = ++revision;
    addressField.value = [first, last].map(localPart).join(".");
    try {
        const response = await fetch("/signup/address", {
            method: "POST",
            body: new URLSearchParams({
                first_name: first,
                last_name: last,
                csrf: firstName.form.elements.csrf.value,
            }),
        });
        if (!response.ok) return;
        const suggestion = await response.text();
        if (automatic && current === revision) addressField.value = suggestion;
    } catch {
        // The local suggestion stays editable if the availability check fails.
    }
}

for (const field of names) {
    field.addEventListener("input", () => {
        revision++;
        if (names.some(name => !name.value.trim())) addressField.readOnly = true;
        if (automatic) addressField.value = "";
    });
    field.addEventListener("blur", suggest);
}
addressField.addEventListener("input", () => {
    revision++;
    automatic = !addressField.value;
});
