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
        resultDiv.innerHTML = `
             <h2>Generic Info for ${data.brand_drug}</h2>
             <p>${data.generic_info}</p>
             <h3>Raw Info</h3>
             <p>${data.raw_info}</p>
         `;
    }
    catch (error) {
        console.error("Error:", error);
        resultDiv.innerText = "An error occurred.";
    }
}));
