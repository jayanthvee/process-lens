import "./styles.css";
import { mountApp } from "./app";

const root = document.getElementById("app");
if (root === null) {
  throw new Error("The page has no #app element to render into.");
}

const app = mountApp(root, { listenToHashChange: true });
void app.refresh();
