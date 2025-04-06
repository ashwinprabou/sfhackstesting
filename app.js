"use strict";
var __awaiter = (this && this.__awaiter) || function (thisArg, _arguments, P, generator) {
    function adopt(value) { return value instanceof P ? value : new P(function (resolve) { resolve(value); }); }
    return new (P || (P = Promise))(function (resolve, reject) {
        function fulfilled(value) { try { step(generator.next(value)); } catch (e) { reject(e); } }
        function rejected(value) { try { step(generator["throw"](value)); } catch (e) { reject(e); } }
        function step(result) { result.done ? resolve(result.value) : adopt(result.value).then(fulfilled, rejected); }
        step((generator = generator.apply(thisArg, _arguments || [])).next());
    });
};
// app.ts
const searchBtn = document.getElementById("searchBtn");
const searchInput = document.getElementById("searchInput");
const resultDiv = document.getElementById("result");
searchBtn.addEventListener("click", () => __awaiter(void 0, void 0, void 0, function* () {
    const brandDrug = searchInput.value.trim();
    if (!brandDrug) {
        alert("Please enter a brand drug name.");
        return;
    }
    try {
        const response = yield fetch("http://localhost:5000/search", {
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
        const data = yield response.json();
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
            .map((info) => `<div class="retailer-box box"><pre>${info}</pre></div>`)
            .join("")}
            </div>
        `;
    }
    catch (error) {
        console.error("Error:", error);
        resultDiv.innerText = "An error occurred.";
    }
}));
