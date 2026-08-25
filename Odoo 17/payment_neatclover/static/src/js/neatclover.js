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
    e.preventDefault()
    const formPopup = document.querySelector("#neatclover_form");
    formPopup.style.display = "block";
    const closeButton = document.querySelector(".neatclover_close_form");
    const form = formPopup.children[1]
    const submitButton = formPopup.children[2]

    if (!window.isFormActivated) {
        // Close form when "Cancel" is clicked
        closeButton.addEventListener("click", function (e) {
            e.preventDefault()
            formPopup.style.display = "none";
        });

        // Create and append form fields dynamically if they don't exist
        function createFormFields() {
            // Check if each field exists, if not create it
            const fieldsData = [
                { id: 'neatclover_email', label: 'Email', type: 'email' },
                { id: 'neatclover_name', label: 'Name', type: 'text' },
                { id: 'neatclover_company', label: 'Company', type: 'text' },
                { id: 'neatclover_phone', label: 'Phone Number', type: 'text' },
            ];

            fieldsData.forEach(field => {
                if (!document.querySelector(`#${field.id}`)) {
                    // Create label element
                    const label = document.createElement('label');
                    label.setAttribute('for', field.id);
                    label.textContent = field.label;

                    // Create input element
                    const input = document.createElement('input');
                    input.setAttribute('type', field.type);
                    input.setAttribute('id', field.id);
                    input.setAttribute('name', field.id);
                    input.setAttribute('placeholder', `Enter your ${field.label.toLowerCase()}`);
                    input.setAttribute('required', 'true');

                    // Append label and input to the form
                    form.appendChild(label);
                    form.appendChild(input);
                }
            });
        }

        // Call the function to create the form fields dynamically
        createFormFields();

        function isValidEmail(email) {
            const emailRegex = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
            return emailRegex.test(email);
        }

        submitButton.addEventListener("click", function (event) {
            event.preventDefault(); // Prevent page reload
            if(submitButton.textContent === "Sending...") {
                return false
            }
            
            const email = document.querySelector("#neatclover_email");
            const company = document.querySelector("#neatclover_company");
            const phone = document.querySelector("#neatclover_phone");
            const name = document.querySelector("#neatclover_name");

            if (!email.value || !isValidEmail(email.value) || !company.value || !phone.value || !name.value) {
                alert("Please fill in all fields.");
                return;
            }
            submitButton.textContent = "Sending..."

            fetch("https://api.sns-software.com/api/AcquirerLicense/contact", {
                method: "POST",
                headers: {
                    "Content-Type": "application/json",
                },
                body: JSON.stringify({ email: email.value, company: company.value, phone: phone.value, name: name.value, provider: "clover" }),
            })
            .then(response => {
                submitButton.textContent = "Submit"
                if (response.ok) {
                    alert("Activation code request sent. We will be in touch shortly.");
                    formPopup.style.display = "none";
                    email.value = ""
                    company.value = ""
                    phone.value = ""
                    name.value = ""
                } else {
                    throw new Error("Failed to send request.");
                }
            })
            .catch(error => {
                console.error("Error:", error);
                alert("Failed to send contact request please try again.");
            });
        });
        window.isFormActivated = true
    }
}