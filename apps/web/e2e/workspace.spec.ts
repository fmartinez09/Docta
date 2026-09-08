import { test, expect, type Page } from "@playwright/test";

async function login(page: Page, role: string, viaLocalhost = false) {
  if (viaLocalhost) {
    const alias = new URL(process.env.DOCTA_WEB_ORIGIN!);
    alias.hostname = "localhost";
    await page.goto(`${alias.origin}/auth/login`);
  } else {
    await page.goto("/");
    await page.getByRole("link", { name: "Entrar a mi espacio" }).click();
  }
  await page.getByRole("link", { name: role, exact: true }).click();
  await expect(page.getByText("Sesión iniciada")).toBeVisible();
  // The session label is server-rendered; wait for the client workspace to load
  // before clicking a control whose handler is attached during hydration.
  await expect(page.getByText("Cargando tus cursos…", { exact: true })).toHaveCount(0);
}

test("teacher publication, student citations, reload, abstention and durable failure", async ({
  page,
  browser,
}, info) => {
  await login(page, "teacher", true);
  await page
    .getByRole("button", { name: "＋ Crear curso", exact: true })
    .click();
  await page.getByLabel("Nombre del nuevo curso").fill("Física · Laboratorio");
  await page.getByRole("button", { name: "Crear", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Física · Laboratorio", exact: true }),
  ).toBeVisible();
  const course = new URL(page.url()).searchParams.get("course");
  expect(course).toBeTruthy();
  await page
    .locator("input[type=file]")
    .setInputFiles(process.env.DOCTA_E2E_PDF!);
  await page.getByRole("button", { name: "Subir PDF", exact: true }).click();
  await page.getByRole("button", { name: "Publicar material →" }).click();
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
  await student.getByRole("button", { name: "Enviar pregunta" }).click();
  await expect(student.locator(".pending-state")).toBeVisible();
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
  await expect(student.locator(".citation summary")).toContainText("Pág. 1");
  await expect(student.locator(".citation small")).toContainText("Versión");
  await student.screenshot({
    path: info.outputPath("student.png"),
    fullPage: true,
  });

  await student
    .getByLabel("Tu pregunta")
    .fill("astronomia galactica desconocida");
  await student.getByRole("button", { name: "Enviar pregunta" }).click();
  await expect(
    student.getByText("· Evidencia insuficiente", { exact: true }),
  ).toBeVisible();
  await student.getByLabel("Tu pregunta").fill("fallo velocidad");
  await student.getByRole("button", { name: "Enviar pregunta" }).click();
  await expect(
    student.getByText("No pudimos completar esta respuesta", { exact: true }),
  ).toBeVisible();
  await student.reload();
  await expect(student.locator(".student-message")).toHaveCount(3);
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
  await student.getByRole("button", { name: "Cerrar sesión ↗" }).click();
  await expect(
    student.getByRole("link", { name: "Entrar a mi espacio" }),
  ).toBeVisible();
  expect((await student.request.get("/api/docta/courses")).status()).toBe(401);
  await context.close();
});
