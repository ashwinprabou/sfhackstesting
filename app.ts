// app.ts
const searchBtn = document.getElementById("searchBtn") as HTMLButtonElement;
const searchInput = document.getElementById("searchInput") as HTMLInputElement;
const resultDiv = document.getElementById("result") as HTMLDivElement;

searchBtn.addEventListener("click", async () => {
  const brandDrug = searchInput.value.trim();
  if (!brandDrug) {
    alert("Please enter a brand drug name.");
    return;
  }

  try {
    const response = await fetch("http://localhost:5000/search", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ brand_drug: brandDrug }),
    });

    if (!response.ok) {
      resultDiv.innerText = "Error fetching drug information.";
      return;
    }

    const data = await response.json();
    // Create the layout:
    // Top row: two side-by-side boxes for brand_info (left) and generic_summary (right)
    // Below: a row of boxes for retailer_info

    resultDiv.innerHTML = `
            <div class="top-row">
                <div id="brand-box" class="box">
                    <h2>Brand Info</h2>
                    <pre>${data.brand_info}</pre>
                </div>
                <div id="generic-box" class="box">
                    <h2>Generic Summary</h2>
                    <pre>${data.generic_summary}</pre>
                </div>
            </div>
            <div id="retailer-row" class="row">
                ${data.retailer_info
                  .map(
                    (info: string) =>
                      `<div class="retailer-box box"><pre>${info}</pre></div>`
                  )
                  .join("")}
            </div>
        `;
  } catch (error) {
    console.error("Error:", error);
    resultDiv.innerText = "An error occurred.";
  }
});
