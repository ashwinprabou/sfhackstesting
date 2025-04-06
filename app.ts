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
    resultDiv.innerHTML = `
             <h2>Generic Info for ${data.brand_drug}</h2>
             <p>${data.generic_info}</p>
             <h3>Raw Info</h3>
             <p>${data.raw_info}</p>
         `;
  } catch (error) {
    console.error("Error:", error);
    resultDiv.innerText = "An error occurred.";
  }
});
