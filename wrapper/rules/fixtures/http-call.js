async function load() {
  await fetch('/widgets');        // POSITIVE
  await axios.get('/gadgets');    // POSITIVE
  computeLocally();               // NEGATIVE
}
