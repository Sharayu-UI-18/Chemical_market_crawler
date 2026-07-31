function getAvailabilityStars(inStock, totalSuppliers){

    const percentage = (inStock / totalSuppliers) * 100;

    if (percentage >= 90) return 5;
    if (percentage >= 70) return 4;
    if (percentage >= 50) return 3;
    if (percentage >= 25) return 2;
    if (percentage > 0) return 1;

    return 0;
}

function getDifficultyStars(score){

    if(score < 100) return 1;
    if(score < 300) return 2;
    if(score < 500) return 3;
    if(score < 700) return 4;

    return 5;
}

function renderStars(stars){

    let s = "";

    for(let i=0;i<5;i++){

        s += i < stars ? "★" : "☆";

    }

    return s;

}

function getEstimatedPrice(inStock, totalSuppliers, complexity){

    const availability =
        (inStock / totalSuppliers) * 100;

    const difficulty =
        getDifficultyStars(complexity);

    const priceScore =
        ((100 - availability) * 0.6) +
        ((difficulty / 5) * 100 * 0.4);

    if(priceScore < 35)
        return "🟢 Low";

    if(priceScore < 65)
        return "🟡 Medium";

    return "🔴 High";

}



document.getElementById("searchBtn").addEventListener("click", async () => {

    const cas = document.getElementById("casInput").value.trim();

    if (!cas) {
        alert("Enter a CAS Number");
        return;
    }
    function getComplexityTier(score){

    if(score == null) return "Unknown";

    if(score < 100) return "Minimal";
    if(score < 200) return "Very Low";
    if(score < 300) return "Low";
    if(score < 400) return "Moderate";
    if(score < 500) return "Medium";
    if(score < 600) return "High";
    if(score < 700) return "Very High";
    if(score < 800) return "Elite";
    if(score < 900) return "Extreme";
    if(score < 1000) return "Maximum Small-Molecule";

    return "Macromolecular";
}

    document.getElementById("results").style.display = "none";
document.getElementById("loading").style.display = "block";

document.getElementById("searchBtn").disabled = true;
document.getElementById("searchBtn").innerHTML = "Analyzing...";

    const response = await fetch("/search", {

        method: "POST",

        headers: {
            "Content-Type": "application/json"
        },

        body: JSON.stringify({
            cas: cas
        })

    });

    const data = await response.json();

    document.getElementById("loading").style.display = "none";

document.getElementById("results").style.display = "block";

document.getElementById("searchBtn").disabled = false;
document.getElementById("searchBtn").innerHTML = "Analyze";

    if (!data.success) {
        alert(data.message);
        return;
    }

    const result = data.result;
    const pubchem = result.pubchem;

    

    //---------------------------------------
    // Summary Card
    //---------------------------------------

    document.getElementById("summaryCard").innerHTML = `
        <div class="card shadow-sm">

            <div class="card-body text-center">

                <h3>Market Availability</h3>

<div class="row text-center mt-4">

    <div class="col-md-4">
        <h1 class="text-success fw-bold">
            ${result.in_stock_count}
        </h1>

        <p>In Stock</p>
    </div>

    <div class="col-md-4">
        <h1 class="text-warning fw-bold">
            ${result.synthesis_on_demand_count}
        </h1>

        <p>Custom Synthesis</p>
    </div>

    <div class="col-md-4">
        <h1 class="text-primary fw-bold">
            ${result.sources_found.length}/${result.sources_checked.length}
        </h1>

        <p>Suppliers Found</p>
    </div>

</div>

            </div>

        </div>
    `;

    //---------------------------------------
    // Compound Card
    //---------------------------------------

    let compound = null;

    for (const supplier in result.results){

        if(result.results[supplier].found){

            compound = result.results[supplier];

            break;

        }

    }

    if(compound){

        document.getElementById("compoundCard").innerHTML = `
        <div class="card shadow-sm">

            <div class="card-header bg-primary text-white">

    <h4 class="card-header">
        🧪 Compound Details
    </h4>

</div>

            

            <div class="card-body">

                <div class="row">

                    <div class="col-md-6">

                        <p><strong>Name:</strong><br>${pubchem?.name ?? compound.product_name ?? "-"}</p>

                        <p><strong>CAS Number:</strong><br>${compound.cas_number ?? "-"}</p>

                        <p><strong>Formula:</strong><br>${pubchem?.formula ?? compound.molecular_formula ?? "-"}</p>

                    </div>

                    <div class="col-md-6">

                        <p><strong>Molecular Weight:</strong><br>${pubchem?.weight ?? compound.molecular_weight ?? "-"}</p>

                        <p>
    <strong>Structural Complexity:</strong><br>

    <span class="fs-4 fw-bold text-primary">
        ${pubchem?.complexity ?? "-"}
    </span>

    <br>

    <span class="badge bg-info text-dark mt-2">
        ${getComplexityTier(pubchem?.complexity)}
    </span>

    <br><br>

    <button
        class="btn btn-sm btn-outline-secondary"
        type="button"
        data-bs-toggle="collapse"
        data-bs-target="#complexityInfo"
        aria-expanded="false">

        What does this mean?

    </button>

    <div class="collapse mt-3" id="complexityInfo">

        <table class="table table-sm table-bordered text-center">

            <thead class="table-light">

                <tr>
                    <th>Score</th>
                    <th>Tier</th>
                </tr>

            </thead>

            <tbody>

                <tr><td>0–99</td><td>Minimal</td></tr>
                <tr><td>100–199</td><td>Very Low</td></tr>
                <tr class="${pubchem?.complexity>=200 && pubchem?.complexity<300 ? 'table-success fw-bold' : ''}">
                    <td>200–299</td>
                    <td>Low</td>
                </tr>
                <tr class="${pubchem?.complexity>=300 && pubchem?.complexity<400 ? 'table-success fw-bold' : ''}">
                    <td>300–399</td>
                    <td>Moderate</td>
                </tr>
                <tr class="${pubchem?.complexity>=400 && pubchem?.complexity<500 ? 'table-success fw-bold' : ''}">
                    <td>400–499</td>
                    <td>Medium</td>
                </tr>
                <tr class="${pubchem?.complexity>=500 && pubchem?.complexity<600 ? 'table-success fw-bold' : ''}">
                    <td>500–599</td>
                    <td>High</td>
                </tr>
                <tr class="${pubchem?.complexity>=600 && pubchem?.complexity<700 ? 'table-success fw-bold' : ''}">
                    <td>600–699</td>
                    <td>Very High</td>
                </tr>
                <tr class="${pubchem?.complexity>=700 && pubchem?.complexity<800 ? 'table-success fw-bold' : ''}">
                    <td>700–799</td>
                    <td>Elite</td>
                </tr>
                <tr class="${pubchem?.complexity>=800 && pubchem?.complexity<900 ? 'table-success fw-bold' : ''}">
                    <td>800–899</td>
                    <td>Extreme</td>
                </tr>
                <tr class="${pubchem?.complexity>=900 && pubchem?.complexity<1000 ? 'table-success fw-bold' : ''}">
                    <td>900–999</td>
                    <td>Maximum Small-Molecule</td>
                </tr>
                <tr class="${pubchem?.complexity>=1000 ? 'table-success fw-bold' : ''}">
                    <td>1000+</td>
                    <td>Macromolecular</td>
                </tr>

            </tbody>

        </table>

    </div>

</p>

                    </div>

                </div>

            </div>

        </div>
        `;

    }

    //---------------------------------------
    // Supplier Table
    //---------------------------------------

    const tbody = document.getElementById("supplierTableBody");

    tbody.innerHTML = "";

    for (const supplier in result.results) {

    const item = result.results[supplier];

    let statusBadge = `<span class="badge bg-secondary">Unknown</span>`;

    if (item.availability === "IN_STOCK") {
        statusBadge = `<span class="badge bg-success">In Stock</span>`;
    }
    else if (item.availability === "SYNTHESIS_ON_DEMAND") {
        statusBadge = `<span class="badge bg-warning text-dark">Custom Synthesis</span>`;
    }
    else if (item.availability === "OUT_OF_STOCK") {
        statusBadge = `<span class="badge bg-danger">Out of Stock</span>`;
    }

    tbody.innerHTML += `
    <tr>

        <td class="fw-semibold">
            ${item.source}
        </td>

        <td>
            ${statusBadge}
        </td>

        <td>
            ${item.catalogue_number ?? "-"}
        </td>

        <td>
            ${item.shipping_condition ?? "-"}
        </td>

        <td>
            ${
                item.product_url
                ? `<a href="${item.product_url}"
                     target="_blank"
                     class="btn btn-outline-primary btn-sm">
                     View Product
                   </a>`
                : "-"
            }
        </td>

    </tr>
    `;
}


    //---------------------------------------
// Recommendation
//---------------------------------------

const availabilityStars =
getAvailabilityStars(
    result.in_stock_count,
    result.sources_checked.length
);

const difficultyStars =
getDifficultyStars(pubchem.complexity);

const estimatedPrice =
getEstimatedPrice(
    result.in_stock_count,
    result.sources_checked.length,
    pubchem.complexity
);

let recommendation = "";

if (result.in_stock_count >= 1) {

    recommendation =
    "Buying is recommended based on the current supplier availability.";

}
else if(result.synthesis_on_demand_count > 0){

    recommendation =
    "Custom synthesis is recommended as the compound is not readily available.";

}
else{

    recommendation =
    "The compound was not found on the searched suppliers. Further market investigation is recommended.";

}

document.getElementById("recommendationCard").innerHTML = `
<div class="card shadow-sm border-0">

    <div class="card-header">
        <h4 class="fw-semibold">
💡 Recommendation
</h4>
    </div>

    <div class="card-body">

        <ul class="list-group list-group-flush">

    <li class="list-group-item">

        <strong>Availability</strong><br>

        <span class="fs-4 text-warning">
            ${renderStars(availabilityStars)}
        </span>

        <small class="text-muted">
            (${Math.round((result.in_stock_count/result.sources_checked.length)*100)}%)
        </small>

    </li>

    <li class="list-group-item">

        <strong>Synthesis Difficulty</strong><br>

        <span class="fs-4 text-warning">
            ${renderStars(difficultyStars)}
        </span>

    </li>

    <li class="list-group-item">

        <strong>Estimated Price</strong><br>

        <span class="fw-bold fs-5">
            ${estimatedPrice}
        </span>

    </li>

</ul>
        <div class="alert alert-success mt-4 mb-0">

            <strong>Recommendation:</strong><br>

            ${recommendation}

        </div>

    </div>

</div>
`;

     

});