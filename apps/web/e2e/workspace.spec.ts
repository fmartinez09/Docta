import { test, expect, type Page } from "@playwright/test";

async function login(page: Page, role: string, viaLocalhost = false) {
  if (viaLocalhost) {
    const alias = new URL(process.env.DOCTA_WEB_ORIGIN!);
    alias.hostname = "localhost";
    await page.goto(`${alias.origin}/auth/login`);
  } else {
    await page.goto("/");
    await page.getByRole("link", { name: "Entrar a Docta" }).click();
  }
  await page.getByRole("link", { name: role, exact: true }).click();
  // Wait for the client workspace to hydrate before clicking its controls.
  await expect(
    page.getByRole("button", { name: "Crear curso", exact: true }).first(),
  ).toBeVisible();
}

test("teacher publication, student citations, reload, abstention and durable failure", async ({
  page,
  browser,
}, info) => {
  await login(page, "teacher", true);
  const createDialog = page.getByRole("dialog", { name: "Crear curso" });
  await expect(async () => {
    if (!(await createDialog.isVisible())) {
      await page.getByRole("button", { name: "Crear curso", exact: true }).first().click();
    }
    await expect(createDialog).toBeVisible({ timeout: 1500 });
  }).toPass({ timeout: 20000 });
  await createDialog.getByLabel("Nombre del curso").fill("Física · Laboratorio");
  await createDialog
    .getByRole("button", { name: "Crear curso", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "Física · Laboratorio", exact: true }),
  ).toBeVisible();
  const course = new URL(page.url()).searchParams.get("course");
  expect(course).toBeTruthy();
  await page.getByRole("button", { name: "Material", exact: true }).click();
  await page
    .locator("input[type=file]")
    .setInputFiles(process.env.DOCTA_E2E_PDF!);
  await page.getByRole("button", { name: "Subir PDF", exact: true }).click();
  await page.getByRole("button", { name: "Publicar material" }).click();
  await expect(
    page.getByText("Publicado · Disponible para el tutor"),
  ).toBeVisible();
  await page.screenshot({
    path: info.outputPath("teacher.png"),
    fullPage: true,
  });
  const enrolled = await page.request.post(
    `${process.env.DOCTA_API_BASE_URL}/test-oidc/enroll`,
    {
      headers: { "x-test-key": process.env.DOCTA_E2E_CONTROL_KEY! },
      data: { course_id: course },
    },
  );
  expect(enrolled.ok()).toBe(true);

  const context = await browser.newContext();
  const student = await context.newPage();
  await login(student, "student");
  await expect(
    student.getByRole("heading", { name: "Física · Laboratorio", exact: true }),
  ).toBeVisible();
  await expect(
    student.getByText("Material del curso", { exact: true }),
  ).toBeVisible();
  await expect(student.getByRole("button", { name: "Subir PDF" })).toHaveCount(
    0,
  );
  expect(
    (await context.cookies()).find((cookie) => cookie.name === "docta_session")
      ?.httpOnly,
  ).toBe(true);
  expect(await student.evaluate(() => localStorage.length)).toBe(0);
  const denied = await student.request.get(
    `/api/docta/courses/${course}/documents`,
  );
  expect(denied.status()).toBe(404);
  const csrf = await student.request.post("/api/docta/courses", {
    headers: { Origin: "https://evil.test", "Idempotency-Key": "csrf" },
    data: { title: "attack" },
  });
  expect(csrf.status()).toBe(403);

  await student.getByLabel("Tu pregunta").fill("velocidad");
  // The optimistic pending indicator can appear before the API has persisted the question.
  // The streaming response headers arrive only after the API accepts and commits it.
  const accepted = student.waitForResponse(
    (response) =>
      response.request().method() === "POST" &&
      /\/api\/docta\/conversations\/[^/]+\/messages$/.test(response.url()) &&
      response.status() === 200,
  );
  await student.getByRole("button", { name: "Enviar pregunta" }).click();
  await expect(student.locator(".pending-state")).toBeVisible();
  await accepted;
  await student.reload();
  await expect(
    student.getByText(
      "Identifica el desplazamiento y el tiempo. ¿Cómo se relacionan?",
      { exact: true },
    ),
  ).toBeVisible();
  await expect(student.locator(".student-message")).toHaveCount(1);
  await student.locator(".citation summary").click();
  await expect(student.locator(".citation blockquote")).toContainText(
    "desplazamiento",
  );
  await expect(student.locator(".citation-heading")).toContainText("Pág. 1");
  await expect(student.locator(".citation small")).toContainText("Versión");
  await student.screenshot({
    path: info.outputPath("student.png"),
    fullPage: true,
  });

  for (const [index, question] of ["desplazamiento", "tiempo"].entries()) {
    await student.getByLabel("Tu pregunta").fill(question);
    await student.getByRole("button", { name: "Enviar pregunta" }).click();
    const turn = student.locator(".assistant-turn").nth(index + 1);
    await expect(turn.getByText("Relaciona desplazamiento y tiempo.")).toBeVisible();
    await expect(turn.locator(".rich-fallback")).toHaveCount(0);
    await expect(turn.getByLabel("Fuentes")).toBeVisible();
    await expect(turn.getByText("Cálculo de la velocidad")).toBeVisible();
    await expect(turn.getByRole("heading", { name: "Ahora te toca razonar" })).toBeVisible();
  }
  const richTurns = student.locator(".assistant-turn");
  const activity = richTurns.nth(2);
  await expect(activity.getByRole("button", { name: "Comprobar mi respuesta" })).toBeDisabled();
  await activity.getByRole("button", { name: "Necesito una pista" }).click();
  await expect(activity.getByText("Revisa qué se divide por el tiempo.")).toBeVisible();
  await activity.getByLabel("Tiempo dividido por desplazamiento").check();
  await activity.getByRole("button", { name: "Comprobar mi respuesta" }).click();
  await expect(activity.getByText("Aún no es correcto. Inténtalo de nuevo.")).toBeVisible();
  await activity.getByLabel("Desplazamiento dividido por tiempo").check();
  await activity.getByRole("button", { name: "Comprobar mi respuesta" }).click();
  await expect(activity.getByText("Correcto.", { exact: false })).toBeVisible();
  await expect(activity.getByText("El material indica desplazamiento dividido por tiempo.")).toBeVisible();
  await student.evaluate(() => {
    Element.prototype.scrollIntoView = function () { this.setAttribute("data-scroll-target", "true"); };
  });
  await richTurns.nth(2).getByRole("button", { name: "Ver fuente" }).click();
  await expect(richTurns.nth(2).getByLabel("Fuentes")).toHaveAttribute("data-scroll-target", "true");
  await expect(richTurns.nth(2).getByLabel("Fuentes")).toHaveJSProperty("open", true);
  await expect(richTurns.nth(1).getByLabel("Fuentes")).not.toHaveAttribute("data-scroll-target", "true");
  await student.getByRole("button", { name: "Enriquecido" }).click();
  await expect(richTurns.nth(2).locator(".rich-fallback")).toBeVisible();
  await richTurns.nth(2).getByLabel("Desplazamiento dividido por tiempo").check();
  await richTurns.nth(2).getByRole("button", { name: "Comprobar mi respuesta" }).click();
  await expect(richTurns.nth(2).getByText("Correcto.", { exact: false })).toBeVisible();
  await student.getByRole("button", { name: "Solo texto" }).click();
  await expect(richTurns.nth(2).getByLabel("Explicación")).toBeVisible();

  await student
    .getByLabel("Tu pregunta")
    .fill("astronomia galactica desconocida");
  await student.getByRole("button", { name: "Enviar pregunta" }).click();
  await expect(
    student.getByText("Evidencia insuficiente", { exact: true }),
  ).toBeVisible();
  await student.getByLabel("Tu pregunta").fill("fallo velocidad");
  await student.getByRole("button", { name: "Enviar pregunta" }).click();
  await expect(
    student.getByText("No pudimos completar esta respuesta", { exact: true }),
  ).toBeVisible();
  await student.reload();
  await expect(student.locator(".student-message")).toHaveCount(5);
  await expect(
    student.getByText("No pudimos completar esta respuesta", { exact: true }),
  ).toBeVisible();
  await student.setViewportSize({ width: 390, height: 844 });
  expect(
    await student.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await student.screenshot({
    path: info.outputPath("mobile.png"),
    fullPage: true,
  });
  await student.getByRole("button", { name: "Abrir navegación" }).click();
  await student.getByRole("button", { name: "Cerrar sesión" }).click();
  await expect(
    student.getByRole("link", { name: "Entrar a Docta" }),
  ).toBeVisible();
  expect((await student.request.get("/api/docta/courses")).status()).toBe(401);
  await context.close();
});
