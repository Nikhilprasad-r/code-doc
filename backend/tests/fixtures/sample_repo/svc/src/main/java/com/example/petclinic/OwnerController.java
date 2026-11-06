package com.example.petclinic;

import java.util.List;
import org.springframework.stereotype.Controller;
import org.springframework.web.bind.annotation.*;

@Controller
@RequestMapping("/owners")
class OwnerController {

    private final OwnerRepository owners;

    OwnerController(OwnerRepository owners) {
        this.owners = owners;
    }

    /**
     * Called before each @RequestMapping annotated method; must not be reported as an endpoint.
     */
    @ModelAttribute("owner")
    public Owner findOwner(@PathVariable(name = "ownerId", required = false) Integer ownerId) {
        return ownerId == null ? new Owner() : this.owners.findById(ownerId);
    }

    @GetMapping("/new")
    public String initCreationForm(Map<String, Object> model) {
        return "owners/createOrUpdateOwnerForm";
    }

    @PostMapping(value = "/new")
    public String processCreationForm(Owner owner) {
        this.owners.save(owner);
        return "redirect:/owners/" + owner.getId();
    }

    @RequestMapping(path = {"/find", "/search"}, method = RequestMethod.GET)
    public String findOwners(Map<String, Object> model) {
        return "owners/findOwners";
    }

    @GetMapping("/{ownerId}")
    public ModelAndView showOwner(@PathVariable("ownerId") int ownerId) {
        return new ModelAndView("owners/ownerDetails");
    }
}
