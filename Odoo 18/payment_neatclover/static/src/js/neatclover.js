function showTermsPopup(e) {
    e.preventDefault();
    const popup = document.querySelector("#neatclover_terms_popup");
    const content = document.querySelector("#neatclover_terms_content");
    popup.style.display = "block";

    if (!window.isTermsPopupActivated) {
        document.querySelector(".neatclover_close_terms").addEventListener("click", function (ev) {
            ev.preventDefault();
            popup.style.display = "none";
        });

        fetch("/payment_neatclover/static/src/terms.html")
            .then(function (response) {
                if (!response.ok) {
                    throw new Error("Failed to load terms");
                }
                return response.text();
            })
            .then(function (html) {
                content.innerHTML = html;
            })
            .catch(function () {
                content.innerHTML = "<p>Unable to load terms and conditions. Please try again later.</p>";
            });

        window.isTermsPopupActivated = true;
    }
}

function showActivationForm(e) {
    e.preventDefault();
    const formPopup = document.querySelector("#neatclover_form");
    formPopup.style.display = "block";
    // Same structure as before: h3, #neatclover_request_form, submit, cancel
    const form = formPopup.children[1];
    const submitButton = formPopup.children[2];
    const closeButton = document.querySelector(".neatclover_close_form");

    if (!window.isFormActivated) {
        closeButton.addEventListener("click", function (ev) {
            ev.preventDefault();
            formPopup.style.display = "none";
        });

        const TURNOVER_OPTIONS = [
            "Under $10,000 / month",
            "$10,000 – $50,000 / month",
            "$50,000 – $100,000 / month",
            "$100,000 – $250,000 / month",
            "$250,000 – $500,000 / month",
            "Over $500,000 / month",
        ];

        function addInputField(parent, id, labelText, type, placeholder) {
            if (document.querySelector("#" + id)) {
                return;
            }
            const label = document.createElement("label");
            label.setAttribute("for", id);
            label.textContent = labelText;
            parent.appendChild(label);

            const input = document.createElement("input");
            input.setAttribute("type", type || "text");
            input.setAttribute("id", id);
            input.setAttribute("name", id);
            input.setAttribute("placeholder", placeholder || ("Enter your " + labelText.toLowerCase()));
            parent.appendChild(input);
        }

        function addSelectField(parent, id, labelText, placeholder) {
            if (document.querySelector("#" + id)) {
                return;
            }
            const label = document.createElement("label");
            label.setAttribute("for", id);
            label.textContent = labelText;
            parent.appendChild(label);

            const select = document.createElement("select");
            select.setAttribute("id", id);
            select.setAttribute("name", id);
            const empty = document.createElement("option");
            empty.value = "";
            empty.textContent = placeholder || "Select...";
            select.appendChild(empty);
            TURNOVER_OPTIONS.forEach(function (opt) {
                const option = document.createElement("option");
                option.value = opt;
                option.textContent = opt;
                select.appendChild(option);
            });
            parent.appendChild(select);
        }

        function addHint(parent, text) {
            const hint = document.createElement("p");
            hint.className = "neatclover-field-hint";
            hint.textContent = text;
            parent.appendChild(hint);
        }

        function createFormFields() {
            if (document.querySelector("#neatclover_mode_tabs")) {
                return;
            }

            // Mode tabs (anchors, same style as original submit/cancel links)
            const tabs = document.createElement("div");
            tabs.id = "neatclover_mode_tabs";
            tabs.className = "neatclover-mode-tabs";

            const companyTab = document.createElement("a");
            companyTab.href = "#";
            companyTab.id = "neatclover_tab_company";
            companyTab.className = "neatclover-mode-tab active";
            companyTab.textContent = "FOR MY COMPANY";

            const clientTab = document.createElement("a");
            clientTab.href = "#";
            clientTab.id = "neatclover_tab_client";
            clientTab.className = "neatclover-mode-tab";
            clientTab.textContent = "FOR A CLIENT";

            tabs.appendChild(companyTab);
            tabs.appendChild(clientTab);
            form.appendChild(tabs);

            // Company pane — visible by default (original fields + turnover)
            const companyPane = document.createElement("div");
            companyPane.id = "neatclover_pane_company";
            companyPane.style.display = "block";
            addInputField(companyPane, "neatclover_email", "Email", "email", "Enter your email");
            addInputField(companyPane, "neatclover_name", "Name", "text", "Enter your name");
            addInputField(companyPane, "neatclover_company", "Company", "text", "Enter your company");
            addInputField(companyPane, "neatclover_phone", "Phone Number", "tel", "Enter your phone number");
            addSelectField(
                companyPane,
                "neatclover_card_turnover",
                "Company Card Turnover (Approx.)",
                "Select or enter your approx. monthly"
            );
            addHint(
                companyPane,
                "Approximate total card-based sales (e.g., credit/debit) handled by the company using this integration."
            );
            form.appendChild(companyPane);

            // Client pane — hidden until tab click
            const clientPane = document.createElement("div");
            clientPane.id = "neatclover_pane_client";
            clientPane.className = "neatclover-client-grid";
            clientPane.style.display = "none";

            const leftCol = document.createElement("div");
            addInputField(leftCol, "neatclover_partner_name", "Partner Name", "text", "Enter your partner name");
            addInputField(leftCol, "neatclover_partner_email", "Partner Email", "email", "Enter your partner email");
            addInputField(leftCol, "neatclover_partner_agency", "Odoo Partner Agency", "text", "Enter your agency name");
            addInputField(leftCol, "neatclover_partner_phone", "Partner Phone", "tel", "Enter your partner phone");
            clientPane.appendChild(leftCol);

            const rightCol = document.createElement("div");
            addInputField(rightCol, "neatclover_client_company", "Client Company Name", "text", "Enter your client company");
            addInputField(rightCol, "neatclover_client_contact", "Client Contact Name", "text", "Enter your client contact");
            addInputField(rightCol, "neatclover_client_email", "Client Email", "email", "Enter your client email");
            addSelectField(
                rightCol,
                "neatclover_client_card_turnover",
                "Client Card Turnover (Approx.)",
                "Select or enter approx. monthly"
            );
            addHint(
                rightCol,
                "Approximate total card-based sales (e.g., credit/debit) for this client using the integration."
            );
            clientPane.appendChild(rightCol);
            form.appendChild(clientPane);

            // Terms agreement under fields
            if (!document.querySelector("#neatclover_terms_notice")) {
                const termsNotice = document.createElement("p");
                termsNotice.id = "neatclover_terms_notice";
                termsNotice.className = "neatclover-terms-notice";
                termsNotice.innerHTML =
                    'By submitting this form you agree to our <a href="#" id="neatclover_terms_link">terms and conditions</a>.';
                form.appendChild(termsNotice);
                document.querySelector("#neatclover_terms_link").addEventListener("click", function (ev) {
                    showTermsPopup(ev);
                });
            }

            window.neatcloverRequestMode = "company";

            function showCompany() {
                window.neatcloverRequestMode = "company";
                companyPane.style.display = "block";
                clientPane.style.display = "none";
                companyTab.classList.add("active");
                clientTab.classList.remove("active");
            }

            function showClient() {
                window.neatcloverRequestMode = "client";
                companyPane.style.display = "none";
                clientPane.style.display = "grid";
                companyTab.classList.remove("active");
                clientTab.classList.add("active");
            }

            companyTab.addEventListener("click", function (ev) {
                ev.preventDefault();
                showCompany();
            });
            clientTab.addEventListener("click", function (ev) {
                ev.preventDefault();
                showClient();
            });
        }

        createFormFields();

        function isValidEmail(email) {
            return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email || "");
        }

        function isValidPhone(phone) {
            if (!phone) {
                return false;
            }
            if (!/^[+\d][\d\s\-().]*$/.test(phone)) {
                return false;
            }
            const digits = phone.replace(/\D/g, "");
            return digits.length >= 7 && digits.length <= 15;
        }

        function val(id) {
            const node = document.querySelector("#" + id);
            return node ? (node.value || "").trim() : "";
        }

        submitButton.addEventListener("click", function (event) {
            event.preventDefault();
            if (submitButton.textContent === "Sending...") {
                return false;
            }

            const mode = window.neatcloverRequestMode || "company";
            let email;
            let name;
            let company;
            let phone;
            let cardTurnover = "";
            let payloadExtras = {};

            if (mode === "company") {
                email = val("neatclover_email");
                name = val("neatclover_name");
                company = val("neatclover_company");
                phone = val("neatclover_phone");
                cardTurnover = val("neatclover_card_turnover");
                if (!email || !isValidEmail(email) || !name || !company || !phone || !isValidPhone(phone) || !cardTurnover) {
                    alert("Please fill in all fields with a valid email and phone number.");
                    return;
                }
            } else {
                name = val("neatclover_partner_name");
                email = val("neatclover_partner_email");
                company = val("neatclover_client_company");
                phone = val("neatclover_partner_phone");
                cardTurnover = val("neatclover_client_card_turnover");
                const partnerAgency = val("neatclover_partner_agency");
                const clientContactName = val("neatclover_client_contact");
                const clientEmail = val("neatclover_client_email");
                if (
                    !email || !isValidEmail(email) || !name || !company || !phone || !isValidPhone(phone) ||
                    !partnerAgency || !clientContactName || !clientEmail || !isValidEmail(clientEmail) || !cardTurnover
                ) {
                    alert("Please fill in all client form fields with a valid email and phone number.");
                    return;
                }
                payloadExtras = {
                    partnerName: name,
                    partnerEmail: email,
                    partnerAgency: partnerAgency,
                    partnerPhone: phone,
                    clientCompany: company,
                    clientContactName: clientContactName,
                    clientEmail: clientEmail,
                };
            }

            submitButton.textContent = "Sending...";
            const body = Object.assign(
                {
                    email: email,
                    name: name,
                    company: company,
                    phone: phone,
                    provider: "clover",
                    requestType: mode,
                    cardTurnover: cardTurnover,
                },
                payloadExtras
            );

            fetch("https://api.sns-software.com/api/AcquirerLicense/contact", {
                method: "POST",
                headers: {
                    "Content-Type": "application/json",
                },
                body: JSON.stringify(body),
            })
                .then(function (response) {
                    submitButton.textContent = "Submit";
                    if (response.ok) {
                        alert("Activation code request sent. We will be in touch shortly.");
                        formPopup.style.display = "none";
                        form.querySelectorAll("input, select").forEach(function (input) {
                            input.value = "";
                        });
                    } else {
                        throw new Error("Failed to send request.");
                    }
                })
                .catch(function (error) {
                    console.error("Error:", error);
                    submitButton.textContent = "Submit";
                    alert("Failed to send contact request please try again.");
                });
        });
        window.isFormActivated = true;
    }

    // Always ensure company pane is shown when opening
    const companyPane = document.querySelector("#neatclover_pane_company");
    const clientPane = document.querySelector("#neatclover_pane_client");
    const companyTab = document.querySelector("#neatclover_tab_company");
    const clientTab = document.querySelector("#neatclover_tab_client");
    if (companyPane) {
        companyPane.style.display = "block";
    }
    if (clientPane) {
        clientPane.style.display = "none";
    }
    if (companyTab) {
        companyTab.classList.add("active");
    }
    if (clientTab) {
        clientTab.classList.remove("active");
    }
    window.neatcloverRequestMode = "company";
}
