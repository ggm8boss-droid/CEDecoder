local addr = getAddress("game.exe+1234")
local values = {health = 100, ammo = 30, name = "player one"}

local function heal(amount)
  values.health = math.min(values.health + amount, 100)
  return values.health
end

for k, v in pairs(values) do
  print(k, v)
end

if addr ~= 0 then
  writeInteger(addr, heal(25))
else
  showMessage("not found: " .. tostring(addr))
end

return heal
